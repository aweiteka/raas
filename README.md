# Docker Registry as a Service

A docker registry service in the cloud that integrates with Pulp and the Red Hat ecosystem. Using a Pulp server the service deploys docker image layers to cloud storage (AWS S3) and OpenShift (Crane).

## Basic Idea

![Alt text](images/federated_registry.png "Registry as a Service")

## Workflow
Below is a typical use of the `raas` tool. The following global options may be used:

* Set configuration branch: `--configenv dev|stage|master` This is an important feature of `raas`. This enables the user to seemlessly switch between environments that are configured and managed completely separate from each other.
* Set log level: `--log DEBUG|INFO`
* Disable commiting configuration after tool runs: `--nocommit` This is typically only used in development.

### Upload image to pulp server

**Prerequisites**

1. access to a pulp server
1. a saved docker image: `docker save <some/image> > some-image.tar`

```
raas pulp-upload <isv> some-image.tar
```

* creates pulp repository for docker content if it doesn't exist
* uploads local tar file

### New setup

**Prerequisites**

1. An OpenShift domain
1. An AWS S3 bucket with read+write permissions.

```
raas setup <isv> --oodomain <domain> --s3bucket <bucket>
```

* Validates openshift domain
* validates AWS S3 bucket access
* creates Crane registry as an OpenShift gear
* validates registry at `/v1/_ping`

### Publish or update an image

**Prerequisites**

1. Setup has been run
1. An image has been uploaded to the pulp server

```
raas publish <isv> <some/image>
```

* Clones deployed openshift crane repo
* downloads image from pulp
* pushes ISV layers to S3
* gets RH metadata
* adds ISV metadata
* git commit, git push to OpenShift

### Status

```
raas status <isv> -a [<some/image>] [--pulp]
```

* Checks domain is present
* checks S3 bucket is present
* clones deployed openshift crane repo `rhc clone ...`
* gets deployed image list
* pulls S3 image list
* validates lists match
* checks crane registry API `/v1/_ping`

## Troubleshooting

The container packaging of this tool has additional troubleshooting tools installed.

1. `[sudo] docker pull aweiteka/raas`
1. See below for docker run command.
1. Use the command-line tools to inspect the system.
    * Run diagnostics: `raas --log DEBUG status ...`
    * List AWS S3 resources: `aws s3 ls s3://mybucket --recursive...`
    * Run OpenShift CLI: `rhc ...`
    * Inspect configuration repo: `git clone ...`

## Installation
The raas tool is intended to be run as a container. State is maintained by the configuration repository. In this way multiple users, including automated processes, can use raas to manage and troubleshoot the registry without workstation dependencies.

The container provides `raas`, `aws`, `rhc` and `git` tools. You may need to edit `~/.openshift.express.conf` to add your username for the `rhc` client to work.

### Requirements

* docker 1.4 or greater
* Private repository of below credentials and other configuration
  * AWS S3 account token
  * OpenShift account token
  * Credentials for Pulp server running version 2.5 or greater

### Setup
NOTE: Most of these steps will be automated with the introduction of the `atomic install|run` tool.

1. Pull container image. The 'latest' tag (assumed) tracks the stable release of the project.

        docker pull aweiteka/raas

1. Create directory for uploading content to pulp

        mkdir /run/docker_uploads

1. Set selinux context for directories mounted into the container:

        sudo chcon -Rv -u system_u -t svirt_sandbox_file_t /run/docker_uploads
        sudo chcon -Rv -u system_u -t svirt_sandbox_file_t $HOME/.ssh

1. Edit your `$HOME/.bashrc` file. This sets an environment variable of read+write private repository and adds an alias for running the container.

        export RAAS_CONF_REPO=ssh://git@github.com:user/private-raas-config.git
        alias raas='sudo docker run -it --rm \
                    -e RAAS_CONF_REPO=$RAAS_CONF_REPO \
                    -v ~/.ssh/id_rsa:/root/.ssh/id_rsa \
                    -v ~/.ssh/id_rsa.pub:/root/.ssh/id_rsa.pub \
                    aweiteka/raas'

1. Source the `.bashrc` file

        source .bashrc

