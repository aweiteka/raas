#!/bin/bash

if [ -z "$1" ]
  then
    echo "Provide an environment to initialize: dev, test, stage, master"
    exit 1
fi

ENV=$1
TMPCFG="/tmp/isv-cert-raas"

mkdir -p $TMPCFG
mkdir /root/.pulp
mkdir /root/.aws

git clone --depth 1 -b $ENV $RAAS_CONF_REPO $TMPCFG

cat << EOF > /root/.aws/credentials
[default]
aws_access_key_id = $(awk '/aws_access_key/ {print $3}' $TMPCFG/raas.cfg)
aws_secret_access_key = $(awk '/aws_secret_access_key/ {print $3}' $TMPCFG/raas.cfg)
EOF

cat << EOF > /root/.pulp/admin.conf
[server]
host = $(awk '/host/ {print $3}' $TMPCFG/raas.cfg)
verify_ssl = $(awk '/verify_ssl/ {print $3}' $TMPCFG/raas.cfg)
EOF

echo "alias pulp=\"pulp-admin -u $(awk '/username/ {print $3}' $TMPCFG/raas.cfg) -p $(awk '/password/ {print $3}' $TMPCFG/raas.cfg)\"" >> /root/.bashrc

echo "Commands initialized for $1 environment: pulp, aws, rhc, git"

source /root/.bashrc

/usr/bin/bash
