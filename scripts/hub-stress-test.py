#!/usr/bin/env python3

import argparse
from concurrent import futures
from datetime import datetime
import functools
import logging
from unittest import mock
import os
import sys
import time

import requests
from requests import adapters
from urllib3.util import retry


LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LOG = logging.getLogger('hub-stress-test')

# POST /users/{name}/servers can take over 10 seconds so be conservative with
# the default timeout value.
DEFAULT_TIMEOUT = 30

# The default timeout for waiting on a server status change (starting/stopping)
SERVER_LIFECYCLE_TIMEOUT = 60

USERNAME_PREFIX = 'hub-stress-test'


def parse_args():
    # Consider splitting this into sub-commands in case you want to be able to
    # scale up and retain servers to do some profiling and then have another
    # command to scale down when done. It could also be useful to have a
    # sub-command to report information about the hub, e.g. current number of
    # users/servers and which of those were created by this tool.
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='''
JupyterHub Stress Test

This will create `--count` number of fake users and notebook servers in batches
defined by the `--batch-size` option in the given JupyterHub `--endpoint`.
It will wait for each notebook server to be considered "ready" by the hub.
By default the created users and servers will be deleted but the `--keep`
option can be used to retain the resources for steady-state profiling. The
`--purge` option is available to delete any previously kept users/servers.

An admin API token is required and may be specified using the
JUPYTERHUB_API_TOKEN environment variable.

Similarly the hub API endpoint must be provided and may be specified using the
JUPYTERHUB_ENDPOINT environment variable.

A `--dry-run` option is available for seeing what the test would look like
without actually making any changes, for example:

  JUPYTERHUB_API_TOKEN=test
  python hub-stress-test.py -v -e http://localhost:8000/hub/api --dry-run
''')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Enable verbose (debug) logging which includes '
                             'logging API response times.')
    parser.add_argument('-b', '--batch-size', default=10, type=int,
                        help='Batch size to use when creating users and '
                             'notebook servers. Note that by default z2jh '
                             'will limit concurrent server creation to 64 '
                             '(see c.JupyterHub.concurrent_spawn_limit) '
                             '(default: 10).')
    parser.add_argument('-c', '--count', default=100, type=int,
                        help='Number of users/servers (pods) to create '
                             '(default: 100).')
    parser.add_argument('-e', '--endpoint',
                        default=os.environ.get('JUPYTERHUB_ENDPOINT'),
                        help='The target hub API endpoint for the stress '
                             'test. Can also be read from the '
                             'JUPYTERHUB_ENDPOINT environment variable.')
    parser.add_argument('-t', '--token',
                        default=os.environ.get('JUPYTERHUB_API_TOKEN'),
                        help='JupyterHub admin API token. Must be a token '
                             'for an admin user in order to create other fake '
                             'users for the scale test. Can also be read from '
                             'the JUPYTERHUB_API_TOKEN environment variable.')
    parser.add_argument('--dry-run', action='store_true',
                        help='If set do not actually make API requests.')
    # Note that with nargs='?' if --log-to-file is specified but without an
    # argument value then it will be True (uses the const value) and we'll
    # generate a log file under /tmp. If --log-to-file is not specified at all
    # then it will default to False and we'll log to stdout. Otherwise if
    # --log-to-file is specified with a command line argument we'll log to that
    # file.
    parser.add_argument('--log-to-file', nargs='?', default=False, const=True,
                        metavar='FILEPATH',
                        help='If set logging will be redirected to a file. If '
                             'no FILEPATH value is provided then a '
                             'timestamp-based log file under /tmp will be '
                             'created. Note that if a FILEPATH value is given '
                             'an existing file will be overwritten.')
    keep_purge_group = parser.add_mutually_exclusive_group()
    keep_purge_group.add_argument(
        '-k', '--keep', action='store_true',
        help='Retain the created fake users/servers once they all created. '
             'By default the script will scale up and then teardown. The '
             'script can be run with --keep multiple times to build on an '
             'existing set of fake users.')
    keep_purge_group.add_argument(
        '-p', '--purge', action='store_true',
        help='Purge all fake stress test users from the environment.')
    args = parser.parse_args()
    return args


def validate(args):
    if args.batch_size < 1:
        raise Exception('--batch-size must be greater than 0')
    if args.count < 1:
        raise Exception('--count must be greater than 0')
    if args.token is None:
        raise Exception('An API token must be provided either using --token '
                        'or the JUPYTERHUB_API_TOKEN environment variable')
    if args.endpoint is None:
        raise Exception('A hub API endpoint URL must be provided either using '
                        '--endpoint or the JUPYTERHUB_ENDPOINT environment '
                        'variable')


def setup_logging(verbose=False, log_to_file=False, args=None):
    filename = None
    if log_to_file:  # If --log-to-file is specified at all this is Truthy
        if isinstance(log_to_file, str):  # A specific file is given so use it.
            filename = log_to_file
        else:  # --log-to-file with no arg so generate a tmp file for logging.
            timestamp = datetime.utcnow().isoformat(timespec='seconds')
            filename = os.path.join(
                '/tmp', f'hub-stress-test-{timestamp}.log')
        print(f'Redirecting logs to: {filename}')
    logging.basicConfig(format=LOG_FORMAT, filename=filename, filemode='w')
    root_logger = logging.getLogger(None)
    root_logger.setLevel(logging.INFO)
    if verbose:
        root_logger.setLevel(logging.DEBUG)
    logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)

    if log_to_file and args:
        # Log the args used to run the script for posterity.
        # Scrub the token though so we don't log it.
        args_dict = dict(vars(args))  # Make sure to copy the vars dict.
        args_dict['token'] = '***'
        LOG.info('Args: %s', args_dict)

    def log_uncaught_exceptions(exc_type, exc_value, exc_traceback):
        root_logger.critical("Uncaught exception",
                             exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = log_uncaught_exceptions


def timeit(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            return f(*args, **kwargs)
        finally:
            LOG.info('Took %.3f seconds to %s',
                     (time.time() - start_time), f.__name__)
    return wrapper


def log_response_time(resp, *args, **kwargs):
    """Logs response time elapsed.

    See: https://requests.readthedocs.io/en/master/user/advanced/#event-hooks

    :param resp: requests.Response object
    :param args: ignored
    :param kwargs: ignored
    """
    LOG.debug('%(method)s %(url)s status:%(status)s time:%(elapsed)ss',
              {'method': resp.request.method,
               'url': resp.url,
               'status': resp.status_code,
               'elapsed': resp.elapsed.total_seconds()})


def get_session(token, dry_run=False):
    if dry_run:
        return mock.create_autospec(requests.Session)
    session = requests.Session()
    session.headers.update({'Authorization': 'token %s' % token})
    # Retry on errors that might be caused by stress testing.
    r = retry.Retry(
        backoff_factor=0.5,
        method_whitelist=False,  # retry on any verb (including POST)
        status_forcelist={
            429,  # concurrent_spawn_limit returns a 429
            503,  # if the hub container crashes we get a 503
            504,  # if the cloudflare gateway times out we get a 504
        })
    session.mount("http://", adapters.HTTPAdapter(max_retries=r))
    session.mount("https://", adapters.HTTPAdapter(max_retries=r))
    if LOG.isEnabledFor(logging.DEBUG):
        session.hooks['response'].append(log_response_time)
    return session


def wait_for_server_to_stop(username, endpoint, session):
    count = 1
    while count <= SERVER_LIFECYCLE_TIMEOUT:
        resp = session.get(endpoint + '/users/%s' % username)
        if resp:
            user = resp.json()
            # When the server is stopped the servers dict should be empty.
            if not user.get('servers') or isinstance(user, mock.Mock):
                return True
            LOG.debug('Still waiting for server for user %s to stop, '
                      'attempt: %d', username, count)
        elif resp.status_code == 404:
            # Was the user deleted underneath us?
            LOG.info('Got 404 while waiting for server for user %s to '
                     'stop: %s', username, resp.content)
            # Consider this good if the user is gone.
            return True
        else:
            LOG.warning('Unexpected error while waiting for server for '
                        'user %s to stop: %s', username, resp.content)
        time.sleep(1)
        count += 1
    else:
        LOG.warning('Timed out waiting for server for user %s to stop after '
                    '%d seconds', username, SERVER_LIFECYCLE_TIMEOUT)
        return False


def stop_server(username, endpoint, session, wait=False):
    resp = session.delete(endpoint + '/users/%s/server' % username,
                          timeout=DEFAULT_TIMEOUT)
    if resp:
        # If we got a 204 then the server is stopped and we should not
        # need to poll.
        if resp.status_code == 204:
            return True
        if wait:
            return wait_for_server_to_stop(username, endpoint, session)
        # We're not going to wait so just return True to indicate that we
        # successfully sent the stop request.
        return True
    else:
        LOG.warning('Failed to stop server for user %s. Response status '
                    'code: %d. Response content: %s', username,
                    resp.status_code, resp.content)
        return False


@timeit
def stop_servers(usernames, endpoint, session, batch_size):
    stopped = {}  # map of username to whether or not the server was stopped
    LOG.debug('Stopping servers for %d users in batches of %d',
              len(usernames), batch_size)
    # Do this in batches in a ThreadPoolExecutor because the
    # `slow_stop_timeout` default of 10 seconds in the hub API can cause the
    # stop action to be somewhat synchronous.
    with futures.ThreadPoolExecutor(
            max_workers=batch_size,
            thread_name_prefix='hub-stress-test:stop_servers') as executor:
        future_to_username = {
            executor.submit(stop_server, username, endpoint, session): username
            for username in usernames
        }
        # as_completed returns an iterator which yields futures as they
        # complete
        for future in futures.as_completed(future_to_username):
            username = future_to_username[future]
            stopped[username] = future.result()
    return stopped


@timeit
def wait_for_servers_to_stop(stopped, endpoint, session):
    """Wait for a set of user servers to stop.

    :param stopped: dict of username to boolean value of whether or not the
        server stop request was successful because if not we don't wait for
        that server; if the boolean value is True then it is updated in-place
        with the result of whether or not the server was fully stopped
    :param endpoint: base endpoint URL
    :param session: requests.Session instance
    """
    LOG.debug('Waiting for servers to stop')
    for username, was_stopped in stopped.items():
        # Only wait if we actually successfully tried to stop it.
        if was_stopped:
            # Update our tracking flag by reference.
            stopped[username] = wait_for_server_to_stop(
                username, endpoint, session)


@timeit
def delete_users_after_stopping_servers(stopped, endpoint, session):
    """Delete users after stopping their servers.

    :param stopped: dict of username to boolean value of whether or not the
        server was successfully stopped
    :param endpoint: base endpoint URL
    :param session: requests.Session instance
    :returns: True if all users were successfully deleted, False otherwise
    """
    LOG.debug('Deleting users now that servers are stopped')
    success = True
    for username, was_stopped in stopped.items():
        resp = session.delete(endpoint + '/users/%s' % username,
                              timeout=DEFAULT_TIMEOUT)
        if resp:
            LOG.debug('Deleted user: %s', username)
        elif resp.status_code == 404:
            LOG.debug('User already deleted: %s', username)
        else:
            LOG.warning('Failed to delete user: %s. Response status code: %d. '
                        'Response content: %s. Was the server stopped? %s',
                        username, resp.status_code, resp.content, was_stopped)
            success = False
    return success


@timeit
def delete_users(usernames, endpoint, session, batch_size=10):
    # Do this in batches by first explicitly stopping all of the servers since
    # that could be asynchronous, then wait for the servers to be stopped and
    # then finally delete the users.
    stopped = stop_servers(usernames, endpoint, session, batch_size)

    # Now wait for the servers to be stopped. With a big list the ones at the
    # end should be done by the time we get to them.
    wait_for_servers_to_stop(stopped, endpoint, session)

    # Now try to delete the users.
    return delete_users_after_stopping_servers(stopped, endpoint, session)


@timeit
def create_users(count, batch_size, endpoint, session, existing_users=[]):
    LOG.info('Start creating %d users in batches of %d at %s',
             count, batch_size, endpoint)
    # POST /users is a synchronous call so the timeout should be the batch size
    # or greater.
    timeout = max(batch_size, DEFAULT_TIMEOUT)
    num_existing_users = len(existing_users)
    index = num_existing_users + 1
    users = []  # Keep track of the batches to create servers.
    while index <= count + num_existing_users:
        # Batch create multiple users in a single request.
        usernames = []
        for _ in range(batch_size):
            usernames.append('%s-%d' % (USERNAME_PREFIX, index))
            index += 1
        # Maybe we should use the single user POST so we can deal with 409s
        # gracefully if we are re-running the script on a set of existing users
        resp = session.post(endpoint + '/users', json={'usernames': usernames},
                            timeout=timeout)
        if resp:
            LOG.debug('Created users: %s', usernames)
            users.append(usernames)
        else:
            LOG.error('Failed to create users: %s. Response status code: %d. '
                      'Response content: %s', usernames, resp.status_code,
                      resp.content)
            try:
                delete_users(usernames, endpoint, session)
            except Exception:
                LOG.warning('Failed to delete users: %s', usernames,
                            exc_info=True)
            raise Exception('Failed to create users.')
    return users


def start_server(username, endpoint, session):
    resp = session.post(endpoint + '/users/%s/server' % username,
                        timeout=DEFAULT_TIMEOUT)
    if resp:
        LOG.debug('Server for user %s is starting', username)
    else:
        # Should we delete the user now? Should we stop or keep going?
        LOG.error('Failed to create server for user: %s. '
                  'Response status code: %d. Response content: %s',
                  username, resp.status_code, resp.content)


@timeit
def start_servers(users, endpoint, session):
    LOG.info('Starting notebook servers')
    for index, usernames in enumerate(users):
        # Start the servers in batches using a ThreadPoolExecutor because
        # the start operation is not totally asynchronous so we should be able
        # to speed this up by doing the starts concurrently. That will also be
        # more realistic to users logging on en masse during an event.
        thread_name_prefix = f'hub-stress-test:start_servers:{index}'
        with futures.ThreadPoolExecutor(
                max_workers=len(usernames),
                thread_name_prefix=thread_name_prefix) as executor:
            for username in usernames:
                executor.submit(start_server, username, endpoint, session)


@timeit
def wait_for_servers_to_start(users, endpoint, session):
    LOG.info('Waiting for notebook servers to be ready')
    # Rather than do a GET for each individual user/server, we could get all
    # users and then filter out any that aren't in our list. However, there
    # could be servers in that list that are ready (the ones created first) and
    # others that are not yet (the ones created last). If we check individually
    # then there is a chance that by the time we get to the end of the list
    # those servers are already ready while we waited for those at the front of
    # the list.
    for usernames in users:
        for username in usernames:
            count = 0  # start our timer
            while count < SERVER_LIFECYCLE_TIMEOUT:
                resp = session.get(endpoint + '/users/%s' % username)
                if resp:
                    user = resp.json()
                    # We don't allow named servers so the user should have a
                    # single server named ''.
                    server = user.get('servers', {}).get('', {})
                    if server.get('ready'):
                        LOG.debug('Server for user %s is ready after %d '
                                  'checks', username, count + 1)
                        break
                    elif not server.get('pending'):
                        # It's possible that the server failed to start and in
                        # that case we want to break the loop so we don't wait
                        # needlessly until the timeout.
                        LOG.error('Server for user %s failed to start. Waited '
                                  '%d seconds but the user record has no '
                                  'pending action. Check the hub logs for '
                                  'details. User: %s', username, count, user)
                        break
                else:
                    LOG.warning('Failed to get user: %s. Response status '
                                'code: %d. Response content: %s', username,
                                resp.status_code, resp.content)
                time.sleep(1)
                count += 1
            else:
                # Should we fail here?
                LOG.error('Timed out waiting for server for user %s to be '
                          'ready after %d seconds', username,
                          SERVER_LIFECYCLE_TIMEOUT)


@timeit
def find_existing_stress_test_users(endpoint, session):
    """Finds all existing hub-stress-test users.

    :param endpoint: base endpoint URL
    :param session: requests.Session instance
    :returns: list of existing hub-stress-test users
    """
    # This could be a lot of users so make the timeout conservative.
    resp = session.get(endpoint + '/users', timeout=120)
    if resp:
        users = resp.json()
        LOG.debug('Found %d existing users in the hub', len(users))
        if users:
            users = list(
                filter(lambda user: user['name'].startswith(USERNAME_PREFIX),
                       users))
            LOG.debug('Found %d existing hub-stress-test users', len(users))
        return users
    else:
        # If the token is bad then we want to bail.
        if resp.status_code == 403:
            raise Exception('Invalid token')
        LOG.warning('Failed to list existing users: %s', resp.content)
        return []


@timeit
def run_stress_test(count, batch_size, token, endpoint, dry_run=False,
                    keep=False):
    session = get_session(token, dry_run=dry_run)
    if batch_size > count:
        batch_size = count
    # First figure out how many existing hub-stress-test users there are since
    # that will determine our starting index for names.
    existing_users = find_existing_stress_test_users(endpoint, session)
    # Create the users in batches.
    users = create_users(count, batch_size, endpoint, session,
                         existing_users=existing_users)
    # Now that we've created the users, start a server for each in batches.
    start_servers(users, endpoint, session)
    # Now that all servers are starting we need to poll until they are ready.
    # Note that because of the concurrent_spawn_limit in the hub we could be
    # waiting awhile. We could also be waiting in case the auto-scaler needs to
    # add more nodes.
    wait_for_servers_to_start(users, endpoint, session)
    # If we don't need to keep the users/servers then remove them.
    if not keep:
        # Flatten the list of lists so we delete all users in a single run.
        usernames = [username for usernames in users for username in usernames]
        LOG.info('Deleting %d users', len(usernames))
        if not delete_users(usernames, endpoint, session, batch_size):
            raise Exception('Failed to delete all users')


@timeit
def purge_users(token, endpoint, dry_run=False):
    session = get_session(token, dry_run=dry_run)
    users = find_existing_stress_test_users(endpoint, session)
    if users:
        usernames = [user['name'] for user in users]
        LOG.info('Deleting %d users', len(usernames))
        if not delete_users(usernames, endpoint, session):
            raise Exception('Failed to delete all users')


def main():
    args = parse_args()
    setup_logging(verbose=args.verbose, log_to_file=args.log_to_file,
                  args=args)
    try:
        validate(args)
    except Exception as e:
        LOG.error(e)
        sys.exit(1)

    try:
        if args.purge:
            purge_users(args.token, args.endpoint, dry_run=args.dry_run)
        else:
            run_stress_test(args.count, args.batch_size, args.token,
                            args.endpoint, dry_run=args.dry_run,
                            keep=args.keep)
    except Exception as e:
        LOG.exception(e)
        sys.exit(128)


if __name__ == "__main__":
    main()
