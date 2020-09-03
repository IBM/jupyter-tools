# jupyter-tools

Collection of tools for working with JupyterHub and notebooks.

## Load Testing

In order to support high load events we have tooling to run stress tests on our JupyterHub deployment.

* [hub-stress-test](scripts/hub-stress-test.py): This script allows scaling up hundreds of fake users and notebook
  servers (pods) at once against a target JupyterHub cluster to see how it responds to sudden load, like event users
  signing on at the beginning of the event. It also allows for scaling up and having a steady state of many users
  to profile the performance of the hub. See [Hub Stress Testing](docs/stress-test.md) for more details.
