#!/usr/bin/env python

import json
import logging
import os
#import re
import requests
import sys
import tarfile

from argparse import ArgumentParser
from boto import connect_s3
from boto.s3.key import Key
from ConfigParser import ConfigParser
from git import Repo
from glob import glob
from tempfile import mkdtemp, NamedTemporaryFile
from urlparse import urlsplit


class PulpTar(object):
    """Models tarfile exported from Pulp"""
    def __init__(self, tarfile):
        self.tarfile = tarfile
        self.tar_tempdir = mkdtemp()

    @property
    def docker_images_dir(self):
        """Temp dir of docker images"""
        return self.tar_tempdir + '/web'

    @property
    def crane_metadata_file(self):
        """Full path to crane metadata file"""
        json_files = glob(self.tar_tempdir + '/*.json')
        if len(json_files) == 1:
            return json_files[0]
        else:
            print 'More than one metadata file found'
            exit(1)

    def extract_tar(self, image_tarfile):
        """Extract tarfile into temp dir"""
        tar = tarfile.open(image_tarfile)
        tar.extractall(path=self.tar_tempdir)
        print 'Extracted tarfile to %s' % self.tar_tempdir
        print self.crane_metadata_file
        tar.close()

    def get_tarfile(self):
        """Get a tarfile plus json metadata from url or local file"""
        parts = urlsplit(self.tarfile)
        if not parts.scheme or not parts.netloc:
            print 'Using local file %s' % self.tarfile
            self.extract_tar(self.tarfile)
        else:
            from urllib2 import Request, urlopen, URLError
            req = Request(self.tarfile)
            try:
                print 'Fetching file via URL %s' % self.tarfile
                response = urlopen(req)
            except URLError as e:
                if hasattr(e, 'reason'):
                    print 'We failed to reach a server.'
                    print 'Reason: ', e.reason
                elif hasattr(e, 'code'):
                    print 'The server couldn\'t fulfill the request.'
                    print 'Error code: ', e.code
            else:
                raw_tarfile = NamedTemporaryFile(mode='wb', suffix='.tar')
                raw_tarfile.write(response.read())
                print 'Write file %s from URL' % raw_tarfile.name
                self.extract_tar(raw_tarfile.name)


class AwsS3(object):
    """Interactions with AWS S3"""
    def __init__(self, **kwargs):
        self.bucket = kwargs['bucket_name']
        self.app = kwargs['app_name']
        self.images_dir = kwargs['images_dir']
        self.mask_layers = kwargs['mask_layers']

    def upload_layers(self, files):
        """Upload image layers to S3 bucket"""
        s3 = connect_s3()
        bucket = s3.create_bucket(self.bucket)
        print 'Created S3 bucket %s' % self.bucket
        print 'Uploading image layers to S3'
        for f, path in files:
            with open(f, 'rb') as f:
                dest = os.path.join((self.app), path)
                key = Key(bucket=bucket, name=dest)
                key.set_contents_from_file(f, replace=True)
                key.set_acl('public-read')
                print 'Successfully uploaded to %s:%s' % (bucket, dest)

    def walk_dir(self, layer_dir):
        """Walk image directory, return list of tuples"""
        files = []
        if os.path.isdir(layer_dir):
            # Walk the directory to get all the files to be uploaded
            for dirpath, _, filenames in os.walk(layer_dir):
                for filename in filenames:
                    layer_id = dirpath.split('/')
                    if layer_id[-1] in self.mask_layers:
                        print 'Skipping layer %s' % layer_id[-1]
                        continue
                    filename = os.path.join(dirpath, filename)
                    files.append((filename, os.path.relpath(filename, layer_dir)))
        else:
            assert os.path.exists(layer_dir), '%s does not exist' % layer_dir
            files.append((layer_dir, os.path.basename(layer_dir)))
        return files


class Openshift(object):
    """Interact with Openshift REST API"""

    def __init__(self, server_url, username, password, domain):
        self._server_url = server_url
        self._username = username
        self._password = password
        self._domain = domain
        #self.app_git_url = kwargs['app_git_url']
        #self.cartridge = kwargs['cartridge']
        # FIXME:
        self.app_name = 'registry'
        self.app_data = None
        #self.cranefile = cranefile

    @property
    def domain(self):
        return self._domain

    @property
    def _env_vars(self):
        """Required environment variables to make crane work on openshift"""
        return [('OPENSHIFT_PYTHON_WSGI_APPLICATION', 'crane/wsgi.py'),
                ('OPENSHIFT_PYTHON_DOCUMENT_ROOT', 'crane/')]

    def _call_openshift(self, url, req_type='get', payload=None):
        if req_type == 'get':
            logging.info('Calling Openshift URL: {0}'.format(url))
            r = requests.get(url, auth=(self._username, self._password))
        elif req_type == 'post':
            logging.info('Posting to Openshift URL: {0}'.format(url))
            r = requests.post(url, auth=(self._username, self._password), data=payload)
        else:
            raise ValueError('Invalid value of "req_type" parameter: {0}'.format(req_type))
        r_json = r.json()

        logging.debug('Openshift HTTP status code: {0}'.format(r.status_code))
        logging.debug('Openshift JSON response:\n{0}'.format(json.dumps(r_json, indent=2)))

        if r_json['messages']:
            msgs = ''
            for m in r_json['messages']:
                msgs += '\n - ' + m['text']
            logging.info('Messages from Openshift response:{0}'.format(msgs))

        return r_json

    def _restart_app(self):
        logging.info('Restarting application')
        payload = {'event': 'restart'}
        self._call_openshift(self.app_data['links']['RESTART']['href'], 'post', payload)

    def _set_env_vars(self, url):
        for var in self._env_vars:
            logging.info('Setting environment variable: {0}'.format(var[0]))
            payload = {'name': var[0],
                       'value': var[1]}
            self._call_openshift(url, 'post', payload)

    def create_app(self):
        """Create an Openshift application"""
        payload = {'name': self.app_name,
                   'cartridge': self.cartridge,
                   #'scale': True,
                   'initial_git_url': self.app_git_url}
        url = self._server_url + '/broker/rest/domains/' + self.domain + '/applications'
        logging.info('Creating OpenShift application')
        text = self._call_openshift(url, 'post', payload)
        logging.info('Created app: {0}'.format(self.app_name))
        #self.app_id = r.text['data']['id']
        #print json.dumps(r.json(), indent=4)
        self.app_data = text['data']
        self._set_env_vars(text['data']['links']['ADD_ENVIRONMENT_VARIABLE']['href'])
        self._restart_app()

    def verify_domain(self):
        """Verify that Openshift domain exists"""
        url = self._server_url + '/broker/rest/domains/' + self.domain
        logging.info('Verifying Openshift domain: {0}'.format(self.domain))
        r_json = self._call_openshift(url)
        if r_json['status'] != 'ok':
            raise Exception('Domain not found: {0}'.format(self.domain))


class Configuration(object):
    """Configuration and utilities"""

    _CONFIG_FILE_NAME = 'raas.cfg'
    _CONFIG_REPO_ENV_VAR = 'RAAS_CONF_REPO'

    def __init__(self, isv):
        """Setup Configuration object.

        Use current working dir as local config if it exists,
        otherwise clone repo based on RAAS_CONF_REPO env var.
        """
        self.isv = isv

        if os.path.isfile(self._CONFIG_FILE_NAME):
            self._conf_dir = os.getcwd()
        else:
            repo_url = os.getenv(self._CONFIG_REPO_ENV_VAR)
            if not repo_url:
                raise Exception('Current working directory does not contain {0} configuration file ' + \
                        'and environment variable {1} is not set.'.format(self._CONFIG_FILE_NAME, self._CONFIG_REPO_ENV_VAR))
            self._conf_dir = mkdtemp()
            self._git_clone(repo_url)

        self._conf_file = os.path.join(self._conf_dir, self._CONFIG_FILE_NAME)
        if not os.path.isfile(self._conf_file):
            raise Exception('Configuration file {0} not found'.format(self._conf_file))
        self._parsed_config = ConfigParser()
        self._parsed_config.read(self._conf_file)

        logging.info('Using conf dir: {0}'.format(self._conf_dir))
        logging.info('Using conf file: {0}'.format(self._conf_file))

        self._setup_isv_config_dirs()
        self._setup_isv_config_file()

    @property
    def isv(self):
        return self._isv

    @isv.setter
    def isv(self, val):
        if not val.isalnum():
            raise ValueError('ISV must contain only alphanumeric characters: {0}'.format(val))
        self._isv = val.lower()
        logging.debug('ISV set to {0}'.format(self.isv))

    @property
    def openshift_conf(self):
        return {'server_url': self._parsed_config.get('openshift', 'server_url'),
                'username'  : self._parsed_config.get('openshift', 'username'),
                'password'  : self._parsed_config.get('openshift', 'password'),
                'domain'    : self._parsed_config.get(self.isv, 'openshift_domain')}

    def _git_clone(self, repo_url):
        """Clone repo using GitPython"""
        logging.info('Clonning git repo to: {0}'.format(self._conf_dir))
        self._config_repo = Repo.clone_from(repo_url, self._conf_dir)

    def _git_add(self, files):
        self._config_repo._index.add(files)

    def _git_commit(self, message):
        self._config_repo._index.commit(message)

    def _git_push(self):
        return self._config_repo.remotes.origin.push()

    def commit_all_changes(self):
        #self._git_add(FIXME)
        #self._git_commit(FIXME)
        #self._git_push()
        raise NotImplemented()

    def _setup_isv_config_dirs(self):
        logdir = os.path.join(self._conf_dir, self.isv, 'logs')
        metadir = os.path.join(self._conf_dir, self.isv, 'metadata')
        if not os.path.exists(logdir):
            logging.info('Creating log dir: {0}'.format(logdir))
            os.makedirs(logdir)
        if not os.path.exists(metadir):
            logging.info('Creating metadata dir: {0}'.format(metadir))
            os.makedirs(metadir)

    def _setup_isv_config_file(self):
        """Setup config file defaults if not provided"""
        if not self._parsed_config.has_section(self.isv):
            logging.info('Creating default ISV section in config file')
            self._parsed_config.add_section(self.isv)
            self._parsed_config.set(self.isv, 'openshift_domain', self.isv)
            self._parsed_config.set(self.isv, 'openshift_app', 'registry')
            self._parsed_config.set(self.isv, 's3_bucket', None)
            with open(self._conf_file, 'w') as configfile:
                self._parsed_config.write(configfile)


def main():
    """Entrypoint for script"""
    isv_args = ['isv']
    isv_kwargs = {'metavar': 'ISV_NAME',
                  'help': 'ISV name matching config file and OpenShift Online domain'}
    parser = ArgumentParser()
    parser.add_argument('-l', '--log', metavar='LOG_LEVEL',
            help='Desired log level. Can be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL. Default is WARNING.')
    parser.add_argument('-n', '--nocommit', action='store_true',
                        help='Do not commit configuration. Development only.')
    subparsers = parser.add_subparsers(help='sub-command help', dest='action')
    status_parser = subparsers.add_parser('status', help='Check configuration status')
    status_parser.add_argument(*isv_args, **isv_kwargs)
    setup_parser = subparsers.add_parser('setup', help='Setup initial configuration')
    setup_parser.add_argument(*isv_args, **isv_kwargs)
    push_parser = subparsers.add_parser('push', help='Push or update an image')
    push_parser.add_argument(*isv_args, **isv_kwargs)
    push_parser.add_argument('image', metavar='IMAGE', help='Image name')
    args = parser.parse_args()

    if args.log:
        log_level = getattr(logging, args.log.upper(), None)
        if isinstance(log_level, int):
            logging.basicConfig(level=log_level)

    try:
        config = Configuration(args.isv)
    except Exception as e:
        logging.critical('Failed to initialize raas: {0}'.format(e))
        sys.exit(1)

    try:
        openshift = Openshift(**config.openshift_conf)
    except Exception as e:
        logging.critical('Failed to initialize Openshift: {0}'.format(e))
        sys.exit(1)

    if args.action in 'status':
        status = True
        try:
            openshift.verify_domain()
            print 'Openshift domain "{0}" looks OK'.format(openshift.domain)
        except Exception as e:
            logging.error('Failed to verify Openshift domain: {0}'.format(e))
            status = False
        if status:
            print 'Status of "{0}" should be OK'.format(config.isv)

    elif args.action in 'setup':
        config.setup_isv_config_dirs()
        config.setup_isv_config_file()

    elif args.action in 'push':
        print 'push', args.image
        #mask_layers = conf_file.get('redhat', 'mask_layers')
        #mask_layers = re.split(',| |\n', mask_layers.strip())
        #pulp = PulpTar(args.tarfile)
        #pulp.get_tarfile()
        #cranefile = pulp.crane_metadata_file
        #kwargs = {'bucket_name': args.bucket_name,
        #          'app_name': args.app_name,
        #          'images_dir': pulp.docker_images_dir,
        #          'mask_layers': mask_layers}
        #s3 = AwsS3(**kwargs)
        #files = s3.walk_dir(pulp.docker_images_dir)
        #s3.upload_layers(files)
        #os = Openshift(**conf_file._sections['openshift'])
        #os.create_app()
        quit()

    if not args.nocommit:
        config.commit_all_changes()

if __name__ == '__main__':
    main()
