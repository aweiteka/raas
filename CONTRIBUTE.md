# Contributing

Pull requests encouraged!

## Resources

* [OpenShift Online API](https://access.redhat.com/documentation/en-US/OpenShift_Online/2.0/html-single/REST_API_Guide/index.html)
* AWS S3 [Boto API](https://boto.readthedocs.org/en/latest/s3_tut.html)
* [GitPython](http://gitpython.readthedocs.org/en/stable/tutorial.html)
* [Docker Registry API](http://docs.docker.com/reference/api/registry_api/)
* [Pulp/Crane Registry](http://docs.docker.com/reference/api/registry_api/)

## Development environment

### Setup
1. Clone this repository: `git clone https://github.com/aweiteka/raas.git`
1. Install Python dependencies: `pip install -r requirements.txt`
1. Make the script executable: `chmod +x raas.py`
1. choose configuration option (below)
1. run tool as `./raas.py ...`

### Configuration

There are two ways to manage the configuration of the environment. To use a local configuration, run `raas` from the directory where the `raas.cfg` directory.

#### Local (recommended for development)
Run `raas` from a configuration directory with `raas.cfg` file. NOTE: a directory for each ISV will be created in this directory with logs and metadata files.

1. Copy config file `cp raas.cfg.template raas.cfg`
1. Edit `raas.cfg` config file.

#### Remote

1. Set environment variable of read+write private repository, for example `export RAAS_CONF_REPO="git@github.com:user/private-raas-config.git"`

## Release process

1. bump VERSION file
1. create new tagged release from master

