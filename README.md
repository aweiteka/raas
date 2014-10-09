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

## Using

```
$ ./raas.py acme.bucket myappname path/to/myapp.tar
Extracted tarfile to /tmp/tmpczrXfS
/tmp/tmpczrXfS/layer-test.json
Successfully uploaded to <Bucket: acmecorp.bucket>:acme-app/b157b77b1a65e87b4f49298557677048b98fed36043153dcadc28b1295920373/layer
Successfully uploaded to <Bucket: acmecorp.bucket>:acme-app/b157b77b1a65e87b4f49298557677048b98fed36043153dcadc28b1295920373/json
Successfully uploaded to <Bucket: acmecorp.bucket>:acme-app/b157b77b1a65e87b4f49298557677048b98fed36043153dcadc28b1295920373/ancestry
Successfully uploaded to <Bucket: acmecorp.bucket>:acme-app/511136ea3c5a64f264b78b5433614aec563103b4d4702f3ba7d4d2698e22c158/layer
Successfully uploaded to <Bucket: acmecorp.bucket>:acme-app/511136ea3c5a64f264b78b5433614aec563103b4d4702f3ba7d4d2698e22c158/json
Successfully uploaded to <Bucket: acmecorp.bucket>:acme-app/511136ea3c5a64f264b78b5433614aec563103b4d4702f3ba7d4d2698e22c158/ancestry
Successfully uploaded to <Bucket: acmecorp.bucket>:acme-app/47d565d7907301d28d8a059da006e0ec100a569c0b0442ab98daf65279a6af68/layer
Successfully uploaded to <Bucket: acmecorp.bucket>:acme-app/47d565d7907301d28d8a059da006e0ec100a569c0b0442ab98daf65279a6af68/json
Successfully uploaded to <Bucket: acmecorp.bucket>:acme-app/47d565d7907301d28d8a059da006e0ec100a569c0b0442ab98daf65279a6af68/ancestry
Successfully uploaded to <Bucket: acmecorp.bucket>:acme-app/34e94e67e63a0f079d9336b3c2a52e814d138e5b3f1f614a0cfe273814ed7c0a/layer
Successfully uploaded to <Bucket: acmecorp.bucket>:acme-app/34e94e67e63a0f079d9336b3c2a52e814d138e5b3f1f614a0cfe273814ed7c0a/json
Successfully uploaded to <Bucket: acmecorp.bucket>:acme-app/34e94e67e63a0f079d9336b3c2a52e814d138e5b3f1f614a0cfe273814ed7c0a/ancestry

```
