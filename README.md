# Docker Registry as a Service

A prototype docker registry service in the cloud that integrates with Pulp and the Red Hat ecosystem. From a Pulp export file the service deploys docker image layers to cloud storage (AWS S3) and OpenShift (Crane).

## Requirements

* Python 2.6 or 2.7
* AWS S3 account
* OpenShift account, create domain
* Private repository of credentials and other configuration

## Installation
1. Install Python dependencies: `pip install -r requirements.txt`
1. Copy config file `cp raas.cfg.template raas.cfg`
1. Set environment variable of read+write private repository, for example `export RAAS_CONF_REPO="git@github.com:user/private-raas-config.git"`

## Configuration
1. Copy the common configuration file

```
cp raas.cfg.template raas.cfg
```

1. Edit raas.cfg config file, commit and push to private repository

```
[redhat]
# layers on that should NOT be uploaded to cloud storage
# intended for Red Hat image layers that are hosted on RH CDN
# comma-, space- or line-separated IDs that match tarball IDs
mask_layers =
    8dc6a04270dfb41460bd53968e31b14da5414501c935fb67cf25975af9066925
    dbb4ad53beb6972d3639a16b4505d06d37695a8a494158d564742c24d94c1067
    bef54b8f8a2fdd221734f1da404d4c0a7d07ee9169b1443a338ab54236c8c91a
    e1f5733f050b2488a17b7630cb038bfbea8b7bdfa9bdfb99e63a33117e28d02f

[openshift]
server_url = https://openshift.redhat.com
app_git_url = https://github.com/aweiteka/crane
# get token from openshift.com or CLI command "rhc authorization list"
auth_token = asdf1234asdf5678
username = openshift_username
password = password
domain = mydomain
cartridge = python-2.7
```

### AWS S3

Assumes an AWS account. Boto API uses local configuration. Create credentials file `~/.aws/credentials` with the following values:

```
[default]
aws_access_key_id = YOUR_ACCESS_KEY_ID
aws_secret_access_key = YOUR_SECRET_ACCESS_KEY
```

## Basic Idea


![Alt text](images/federated_registry.png "Registry as a Service")

## In Use

```
$ ./raas.py bigdatainc bigdata-app
Using local file bigdata-app.tar
Extracted tarfile to /tmp/tmpgDuAh6
/tmp/tmpgDuAh6/bigdata-app.json
Skipping layer e1f5733f050b2488a17b7630cb038bfbea8b7bdfa9bdfb99e63a33117e28d02f
Skipping layer e1f5733f050b2488a17b7630cb038bfbea8b7bdfa9bdfb99e63a33117e28d02f
Skipping layer e1f5733f050b2488a17b7630cb038bfbea8b7bdfa9bdfb99e63a33117e28d02f
Created S3 bucket bigdatainc.bucket
Uploading image layers to S3
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/bf3bf3a4371d50acc13a8774e5cd3d9e2ebc4818252893043f93397d69e0ada8/layer
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/bf3bf3a4371d50acc13a8774e5cd3d9e2ebc4818252893043f93397d69e0ada8/json
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/bf3bf3a4371d50acc13a8774e5cd3d9e2ebc4818252893043f93397d69e0ada8/ancestry
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/9f87f5b67e6a56ba461cee756b88401b0584d21947b442eb3cc4638edd65c6c2/layer
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/9f87f5b67e6a56ba461cee756b88401b0584d21947b442eb3cc4638edd65c6c2/json
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/9f87f5b67e6a56ba461cee756b88401b0584d21947b442eb3cc4638edd65c6c2/ancestry
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/769aa15f7c47c8f6ed1b09615ff3d26943492de2b2c588898ddb5e62d1675c49/layer
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/769aa15f7c47c8f6ed1b09615ff3d26943492de2b2c588898ddb5e62d1675c49/json
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/769aa15f7c47c8f6ed1b09615ff3d26943492de2b2c588898ddb5e62d1675c49/ancestry
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/6691d2df8395e6ad20fb3c1acaefeddd4c6eebf8724909aea79927e2f1614b26/layer
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/6691d2df8395e6ad20fb3c1acaefeddd4c6eebf8724909aea79927e2f1614b26/json
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/6691d2df8395e6ad20fb3c1acaefeddd4c6eebf8724909aea79927e2f1614b26/ancestry
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/661e33d746676c865b7f19f931f61ebe1f3c1092cfc76644332c427acda6972c/layer
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/661e33d746676c865b7f19f931f61ebe1f3c1092cfc76644332c427acda6972c/json
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/661e33d746676c865b7f19f931f61ebe1f3c1092cfc76644332c427acda6972c/ancestry
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/2d723127d3c6301b9d9316eea45e6a379ebd941c795e52865e32954ddb0a8a1d/layer
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/2d723127d3c6301b9d9316eea45e6a379ebd941c795e52865e32954ddb0a8a1d/json
Successfully uploaded to <Bucket: bigdatainc.bucket>:bigdata-app/2d723127d3c6301b9d9316eea45e6a379ebd941c795e52865e32954ddb0a8a1d/ancestry
Creating OpenShift application
Created app registry
Setting environment variable OPENSHIFT_PYTHON_WSGI_APPLICATION
Setting environment variable OPENSHIFT_PYTHON_DOCUMENT_ROOT
restarting application

```

# Workflow

## Primary commands

### New setup

Prerequisite: create OpenShift domain, ensure S3 bucket setup with read+write access.

```
raas setup <isv>
```

* Validate openshift domain
* validate AWS S3 bucket access
* get target crane release tar, init git repo with openshift online remote
* create gear
* validate registry at `/v1/_ping`

### Push or update image

```
raas push <isv> <image>
```

* Clone deployed openshift crane repo
* get image from pulp
* push ISV layers to S3
* get RH metadata files
* add ISV metadata
* git commit, git push to OpenShift
* backup ISV metadata to config repo, git commit, git push config repo

### Status

```
raas status <isv>
```

* Check domain is present
* check S3 bucket is present
* clone deployed openshift crane repo `rhc clone ...`
* get deployed image list
* pull S3 image list
* validate lists match
* check crane registry API `/v1/_ping`
* run linter on json metadata files

## Troubleshooting

Use standard tools for troubleshooting

* `aws s3 ...`
* `rhc ...`
* `git commit|push ...`
