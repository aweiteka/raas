# Docker Registry as a Service

A prototype docker registry service in the cloud that integrates with Pulp and the Red Hat ecosystem. From a Pulp export file the service deploys docker image layers to cloud storage (AWS S3) and OpenShift (Crane).

## Requirements

* Python 2.6 or 2.7
* AWS S3 account
* OpenShift account

## Installation
`pip install -r requirements.txt`

## Configuration

### AWS S3

Assumes an AWS account. Create credentials file `~/.aws/credentials` with the following values:

```
[default]
aws_access_key_id = YOUR_ACCESS_KEY_ID
aws_secret_access_key = YOUR_SECRET_ACCESS_KEY
```

### OpenShift

TBA

