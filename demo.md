# Registry as a Service demonstration

## Setup
* AWS S3 with no buckets
* OpenShift Online with no apps running

## Visual
* Browser tabs
  1. AWS S3 dashboard
  1. OpenShift Online applications list
* Terminal tabs
  1. raas.py script `./raas.py ...`
  2. docker cmds
* Slide or image: visual representation of storage, CDN, openshift, end-user docker client

## Workflow
1. Show AWS S3 dashboard
1. Show openshift online dashboard
1. Run raas.py script pointing to output of certification service (pulp export)
1. Show AWS S3 bucket
1. Show OpenShift application
1. Show diagram
1. `docker pull <crane_url>/<app_name>`
1. `docker run <app_name>`

## Prep (FIXME: Items to automate)
1. Update pulp repo redirect URL
   ```pulp-admin docker repo update --repo-id acme-app --redirect-url https://s3.amazonaws.com/acmecorp.bucket/acme-app/```
1. Export tar file
   ```pulp-admin docker repo export run --repo-id acme-app --export-file /var/lib/pulp/static/acme-app.tar```
