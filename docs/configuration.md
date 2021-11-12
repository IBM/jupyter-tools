# Configuration settings

This document provides an overview of configuration settings for increasing hub performance.

1. [Culler settings](#culler)
   1. [Frequency](#culler-frequency)
   2. [Concurrency limit](#culler-concurrency)
   3. [Timeout](#culler-timeout)
   4. [Notebook culler](#notebook-culler)
2. [Activity intervals](#activity)
   1. [`activity_resolution`](#activity-resolution)
   2. [`last_activity_interval`](#last-activity-interval)
   3. [`JUPYERHUB_ACTIVITY_INTERVAL`](#hub-activity-interval)
3. [Startup time](#startup)
   1. [`init_spawners_timeout`](#spawners-timeout)
4. [Other settings](#other)
   1. [`k8s_threadpool_api_workers`](#kubespawner-thread)
   2. [Disable events](#kubespawner-events)
   3. [Disable consecutiveFailureLimit](#disable-consecutivefailurelimit)
   4. [Increase http_timeout](#increase-http-timeout)
5. [References](#references)


<a name="culler"></a>
## Culler settings
There are two mechanisms for controlling the culling of servers and users. One is a
process managed by the hub which will periodically cull users and servers. The other
is a setting which will allow servers to delete themselves after a period of inactivity.

<a name="culler-frequency"></a>
### Frequency
By default the culler runs every 10 minutes. With a more aggressive setting for the notebook
idle timeout the hub-managed culler can be run less frequently.

<a name="culler-concurrency"></a>
### Concurrency limit
By default the culler has a concurrency limit of 10. This means it will make up to 10
concurrent API calls. When deleting a large number of users that can generate a high load
on the hub. Setting this to `1` helps to reduce load on the hub.

<a name="culler-timeout"></a>
### Timeout
The timeout controls how long a server can be idle before being deleted. Because the servers
will aggressively cull themselves this value can be set very high.

These can be all configured in the `cull` section of [values.yaml](https://github.com/jupyterhub/zero-to-jupyterhub-k8s/blob/master/jupyterhub/values.yaml):
```yaml
cull:
  timeout: 432000 # 5 days
  every: 3600 # Run once an hour instead of every 10 minutes
  concurrency: 1
```

<a name="notebook-culler"></a>
### Notebook culler
There are two settings which control how the notebooks cull themselves. The first is
`c.NotebookApp.shutdown_no_activity_timeout` which specifies the period of inactivity
(in seconds) before a server is shutdown. The second is `c.MappingKernelManager.cull_idle_timeout`
which determines when kernels will be shutdown. These settings can be configured as described
[here](https://jupyter-notebook.readthedocs.io/en/stable/config_overview.html).

<a name="activity"></a>
## Activity intervals
These settings control how spawner and user activity is tracked. These settings have
a large impact on the performance of the hub.

<a name="activity-resolution"></a>
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

<a name="last-activity-interval"></a>
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

<a name="hub-activity-interval"></a>
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

<a name="startup"></a>
## Startup time

<a name="spawners-timeout"></a>
### `init_spawners_timeout`
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

<a name="other"></a>
## Other settings
Other settings which are helpful for tuning performance.

<a name="kubespawner-thread"></a>
### `c.KubeSpawner.k8s_api_threadpool_workers`
This value controls the number of threads `kubespawner` will create to make API calls to
Kubernetes. The default is `5 * num_cpus`. Given a large enough number of users logging in
and spawning servers at the same time this may not be enough threads. A more sensible value
for this setting is [c.Jupyterhub.concurrent_spawn_limit](https://jupyterhub.readthedocs.io/en/stable/api/app.html#jupyterhub.app.JupyterHub.concurrent_spawn_limit).
`concurrent_spawn_limit` controls how many users can spawn servers at the same time.
By creating that many threadpool workers we ensure that there's always a thread available
to service a user's spawn request. The upstream default for `concurrent_spawn_limit` is 100 while
the default with Zero to JupyterHub is 64.

In `values.yaml`:
```yaml
extraConfig:
  perfConfig: |
     c.KubeSpawner.k8s_api_threadpool_workers = c.JupyterHub.concurrent_spawn_limit
```

<a name="kubespawner-events"></a>
### Disable user events
With this enabled `kubespawner` will process events from the Kubernetes API which are then
used to show progress on the user spawn page. Disabling this reduces the load on `kubespawner`.

To disable user events update the `events` key in the `values.yaml` file. This value ultimately
sets `c.KubeSpawner.events_enabled`.

```yaml
singleuser:
  events: false
```

<a name="disable-consecutivefailurelimit"></a>
### Disable consecutiveFailureLimit
JupyterHub itself defaults [c.Spawner.consecutive_failure_limit](https://jupyterhub.readthedocs.io/en/stable/api/spawner.html#jupyterhub.spawner.Spawner.consecutive_failure_limit) to 0 to disable it but zero-to-jupyterhub-k8s
defaults it to [5](https://github.com/jupyterhub/zero-to-jupyterhub-k8s/blob/0.11.0/jupyterhub/values.yaml#L43).
This can be problematic when a large user event starts and many users are starting server pods at the same time
if the user node capacity is exhausted and, for example, spawns timeout due to waiting on the node auto-scaler adding
more user node capacity. When the consecutive failure limit is reached the hub will restart which probably will not
help with this type of failure scenario when pod spawn timeouts are occurring because of capacity issues.

To disable the consecutive failure limit update the `consecutiveFailureLimit` key in the `values.yaml` file.

```yaml
hub:
  consecutiveFailureLimit: 0
```

<a name="increase-http-timeout"></a>
### Increase http_timeout

[`c.KubeSpawner.http_timeout`](https://jupyterhub.readthedocs.io/en/stable/api/spawner.html#jupyterhub.spawner.Spawner.http_timeout)
defaults to 30 seconds. During scale and load testing we have seen that sometimes
we can hit this timeout and the hub will delete the server pod but if we had just waited a few seconds more it
would have been enough. So if you have node capacity so that pods are being created, but maybe they are just
slow to come up and are hitting this timeout, you might want to increase it to something like 60 seconds. This
also seems to vary depending on whether you are using `notebook` or `jupyterlab` / `jupyter-server`, the type of
backing storage for the user pods (i.e. s3fs shared object storage is known to be slow(er)), and how many and what kinds of
extensions you have in the user image.

<a name="references"></a>
## References
- https://discourse.jupyter.org/t/confusion-of-the-db-instance/3878
- https://discourse.jupyter.org/t/identifying-jupyterhub-api-performance-bottleneck/1289
- https://discourse.jupyter.org/t/minimum-specs-for-jupyterhub-infrastructure-vms/5309
- https://discourse.jupyter.org/t/background-for-jupyterhub-kubernetes-cost-calculations/5289
- https://discourse.jupyter.org/t/core-component-resilience-reliability/5433
- https://discourse.jupyter.org/t/scheduler-insufficient-memory-waiting-errors-any-suggestions/5314
