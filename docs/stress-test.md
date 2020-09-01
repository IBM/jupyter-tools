# Hub Stress Testing

This document gives an overview of the [hub-stress-test script](../scripts/hub-stress-test.py)
and how it can be used.

## Setup

You will need two things to run the script, an admin token and a target hub API endpoint URL.

The admin token can be provided to the script on the command line but it's recommended to create a
file from which you can source and export the `JUPYTERHUB_API_TOKEN` environment variable.

For the hub API endpoint URL, you can probably use the same value as the `JUPYTERHUB_API_URL`
environment variable in your user notebooks, e.g. `https://myhub-testing.acme.com/hub/api`.

Putting these together, you can have a script like the following to prepare your environment:

```bash
#!/bin/bash -e
export JUPYTERHUB_API_TOKEN=abcdef123456
export JUPYTERHUB_ENDPOINT=https://myhub-testing.acme.com/hub/api
```

## Scaling up

By default the `hub-stress-test` script will scale up to 100 users and notebook servers (pods)
in batches, wait for them to be "ready" and then stop and delete them.

### Placeholders and user nodes

The number of pods that can be created in any given run depends on the number of
`user-placeholder` pods already in the cluster and the number of `user` nodes. The
`user-placeholder` pods are pre-emptible pods which are part of a StatefulSet:

```console
$ kubectl get statefulset/user-placeholder -n jhub
NAME               READY     AGE
user-placeholder   300/300   118d
```

We normally have very few of these in our testing cluster but need to
scale them up when doing stress testing otherwise the `hub-stress-test` script has to wait
for the auto-scaler to add more nodes to the `user` worker pool. The number of available
workers can be found like so:

```console
$ kubectl get nodes -l workerPurpose=users | grep -c "Ready\s"
13
```

The number of `user` nodes needed for a scale test will depend on the resource requirements
of the user notebook pods, reserved space on the nodes, other system pods running on the nodes,
e.g. logging daemon, pod limits per node, etc.

If there are not enough nodes available and the auto-scaler has to create them on the fly
as the stress test is running, we can hit the [consecutive failure limit](https://github.com/jupyterhub/zero-to-jupyterhub-k8s/blob/363d0b7db5/jupyterhub/values.yaml#L17) which will cause the hub container to crash and restart.
One way to avoid this is run the script with a `--count` that is not higher than 500 which
gives time between runs for the auto-scaler to add more `user` nodes.

As an example, on IBM Cloud there is a hard [pod limit](https://cloud.ibm.com/docs/containers?topic=containers-limitations)
of 110 per node and there are about 25 system pods per node. Our testing cluster user notebooks are using a micro
profile so their resource usage is not an issue, they are just limited to the 110 pod-per-node limit.
As a reference, to scale up to 3000 users/pods we need to have at least 35 user nodes.

### Steady state testing

The `--keep` option can be used to scale up the number of pods in the cluser and retain them
so that you can perform tests or profiling on the hub with a high load. When the script runs
it will first check for the number of existing `hub-stress-test` users and start creating new
users based on an index so you can run the script with a `--count` value of 200-500 if you need
to let the auto-scaler add `user` nodes after each run.

Note that the `c.NotebookApp.shutdown_no_activity_timeout` value in the user notebook image (in the
testing cluster) should either be left at the default (0) or set to some larger window so that while
you are scaling up the notebook pods do not shut themselves down.

## Scaling down

If you used the `--keep` option to scale up and retain pods for steady state testing, when you are
done you can scale down the pods and users by using the `--purge` option. The users created by the
script all have a specific naming convention so it knows which notebook servers to stop and users
to remove.

## Monitoring

Depending on the number of pods being created or deleted the script can take awhile. During a run
there are some dashboards you should be watching and also the hub logs. The logging and monitoring
platform is deployment-specific but the following are some examples of dashboards we monitor:

* `Jupyter Notebook Health (Testing)`
  This dashboard shows the active user notebook pods, nodes in the cluster and `user-placeholder`
  pods. This is mostly interesting to watch the active user notebook pods go up or down when scaling
  up or down with the script. The placeholders and user nodes may also fluctuate as placeholder pods
  are pre-empted and as the auto-scaler is adding or removing user nodes.

  ![hub-stress-test-health](images/hub-stress-test-health.png)

* `Jupyter Hub Golden Signals (testing)`
  This is where you can monitor the response time and request rate on the hub. As user notebook pods
  are scaled up each of those pods will "check in" with the hub to report their activity. By default
  each pod checks in with the hub every [5 minutes](https://github.com/jupyterhub/jupyterhub/blob/5dee864af/jupyterhub/singleuser.py#L463). So we expect that the more active user notebook pods in the cluster will increase
  the request rate and increase the response time in this dashboard. The error rates may also increase
  as we get 429 responses from the hub while scaling up due to the [concurrentSpawnLimit](https://github.com/jupyterhub/zero-to-jupyterhub-k8s/blob/363d0b7db/jupyterhub/values.yaml#L16). Those 429 responses are expected
  and the `hub-stress-test` script is built to retry on them. Here is an example of a 3000 user load
  run:

  ![hub-stress-test-request-response-times](images/hub-stress-test-request-response-times.png)

  That run started around 2:30 and then the purge started around 9 which is why response times track
  the increase in request rates. As the purge runs the number of pods reporting activity is going down
  so the request rate also goes down. One thing to note on the purge is that the [slow_stop_timeout](https://github.com/jupyterhub/jupyterhub/blob/42adb4415/jupyterhub/handlers/base.py#L761) defaults to 10 seconds so as
  we are stopping user notebook servers (deleting pods) the response times spike up because of that
  arbitrary 10 second delay in the hub API.

  Other useful panels on this dashboard are for tracking CPU and memory usage of the hub. From the same
  3000 user run as above:

  ![hub-stress-test-resource-usage](images/hub-stress-test-resource-usage.png)

  CPU, memory and network I/O increase as the number of user notebook pods increases and are reporting
  activity to the hub. The decrease in CPU and network I/O are when the purge starts running. Note that
  memory usage remains high even after the purge starts because the hub aggressively caches DB state in
  memory and is apparently not cleaning up the cache references even after spawners and users are deleted
  from the database.
