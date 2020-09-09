# Configuration settings

This document provides an overview of configuration settings for increasing hub performance.

## Culler settings
There are two mechanisms for controlling the culling of servers and users. One is a
process managed by the hub which will periodically cull users and servers. The other
is a setting which will allow servers to delete themselves after a period of inactivity.

### Frequency
By default the culler runs every 10 minutes. With a more aggressive setting for the notebook
idle timeout the hub-managed culler can be run less frequently.

### Concurrency limit
By default the culler has a concurrency limit of 10. This means it will make up to 10
concurrent API calls. When deleting a large number of users that can generate a high load
on the hub. Setting this to `1` helps to reduce load on the hub.

### Timeout
The timeout controls how long a server can be idle before being deleted. Because the servers
will aggressively cull themselves this value can be set very high.

These can be all configured in the `cull` section of `values.yaml`:
```yaml
cull:
  timeout: 432000 # 5 days
  every: 3600 # Run once an hour instead of every 10 minutes
  concurrency: 1
```

### Notebook culler
How long a server can be idle before culling itself is controlled by the `IDLE_TIMEOUT`
environment variable. This is configured in `singleuser.extraEnv` in `values.yaml`.

```yaml
singleuser:
  extraEnv:
    IDLE_TIMEOUT: 14400 # 4 hours
```

## Activity intervals
These settings control how spawner and user activity is tracked. These settings have
a large impact on the performance of the hub.

### `c.JupyterHub.activity_resolution`
Activity resolution controls how often activity updates are written to the database. Many
API calls will record activity for a user. This setting determines whether or not that update
is written to the database. If the update is more recent than `activity_resolution` seconds
ago it's ignored. Increasing this value will reduce commits to the database.

```yaml
extraConfig:
  myConfig: |
    c.JupyterHub.activity_resolution = 6000
```

### `c.JupyterHub.last_activity_interval`
This setting controls how often a periodic task in the hub named `update_last_activity`
runs. This task updates user activity using information from the proxy. This task makes
a large number of database calls and can put a fairly significant load on the hub. Zero to
Jupyterhub sets this to 1 minute by default. The upstream default of 5 minutes is a better
setting.

```yaml
extraConfig:
  myConfig: |
    c.JupyterHub.last_activity_interval = 300
```

### `JUPYTERHUB_ACTIVITY_INTERVAL`
This controls how often each server reports its activity back to the hub. The default
is 5 minutes and with hundreds or thousands of users posting activity updates it puts
a heavy load on the hub and the hub's database. Increasing this to one hour or more
reduces the load placed on the hub by these activity updates.

```yaml
singleuser:
  extraEnv:
    JUPYTERHUB_ACTIVITY_INTERVAL: "3600"
```

## Startup time

### `init_spanwners_timeout`
[c.JupyterHub.init_spawners_timeout](https://jupyterhub.readthedocs.io/en/stable/api/app.html#jupyterhub.app.JupyterHub.init_spawners_timeout) controls how long the hub will wait for spawners to
initialize. When this timeout is reached the spawner check will go into the background and
hub startup will continue. With many hundreds or thousands of spawners this is always going
to exceed any reasonable timeout so there's no reason to wait at all. Setting it to `1` 
(which is the minimum value) allows the hub to start faster and start servicing other requests.

In `values.yaml`:
```yaml
extraConfig:
  myConfig: |
     c.JupyterHub.init_spawners_timeout = 1
```

## Other settings
Other settings which are helpful for tuning performance.

### `c.KubeSpawner.k8s_api_threadpool_workers`
This value controls the number of threads `kubespawner` will create to make API calls to
Kubernetes. The default is `5 * num_cpus`. Given a large enough number of users logging in
and spawning servers at the same time this may not be enough threads. A more sensible value
for this setting is `c.Jupyterhub.concurrent_spawn_limit`. `concurrent_spawn_limit` controls
how many users can spawn servers at the same time. By creating that many threadpool workers
we ensure that there's always a thread available to service a user's spawn request.

In `values.yaml`:
```yaml
extraConfig:
  perfConfig: |
     c.KubeSpawner.k8s_api_threadpool_workers = c.JupyterHub.concurrent_spawn_limit
```

### Disable user events
With this enabled `kubespawner` will process events from the Kubernetes API which are then
used to show progress on the user spawn page. Disabling this reduces the load on `kubespawner`.

To disable user events update the `events` key in the `values.yaml` file.
```yaml
singleuser:
  events: false
```
