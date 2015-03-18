#!/bin/bash

if [[ -z "$1" || "$1" == "-h" || "$1" == "--help" ]]
  then
    echo "USAGE: `basename $0` <environment> ['optional command to execute then exit']"
    echo "You must specify which <environment> to initialize: dev, test, stage, master"
    exit
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

$PULP_ALIAS="pulp-admin -u $(awk '/username/ {print $3}' $TMPCFG/raas.cfg) -p $(awk '/password/ {print $3}' $TMPCFG/raas.cfg)\""
echo "$PULP_ALIAS" >> /root/.bashrc

echo "Commands initialized for $1 environment: pulp, aws, rhc, git"

source /root/.bashrc

if [ -n "$2" ]
  then
    $2
    exit
  else
    /usr/bin/bash
fi
