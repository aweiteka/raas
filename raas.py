#!/usr/bin/env python

import json
import logging
import os
import re
import requests
import sys
import tarfile

from argparse import ArgumentParser
from boto import connect_s3
from boto.s3.key import Key
from ConfigParser import ConfigParser
from git import Repo
from glob import glob
from shutil import rmtree
from tempfile import mkdtemp, NamedTemporaryFile
from urlparse import urlsplit

class PulpServer(object):
    """Interact with Pulp API"""
    def __init__(self, server_url, username, password, verify_ssl):
        self._server_url = server_url
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._web_distributor = "docker_web_distributor_name_cli"
        self._export_distributor = "docker_export_distributor_name_cli"
        self._importer = "docker_importer"
        self._export_dir = "/var/www/pub/docker/web/"
        self._unit_type_id = "docker_image"
        self._chunk_size = 1048576 # 1 MB per upload call

    def _call_pulp(self, url, req_type='get', payload=None):
        if req_type == 'get':
            logging.info('Calling Pulp URL "{0}"'.format(url))
            r = requests.get(url, auth=(self._username, self._password), verify=self._verify_ssl)
        elif req_type == 'post':
            logging.info('Posting to Pulp URL "{0}"'.format(url))
            if payload:
                logging.debug('Pulp HTTP payload:\n{0}'.format(json.dumps(payload, indent=2)))
            r = requests.post(url, auth=(self._username, self._password), data=json.dumps(payload), verify=self._verify_ssl)
        elif req_type == 'put':
            logging.info('Putting to Pulp URL "{0}"'.format(url))
            if payload:
                logging.debug('Pulp HTTP payload:\n{0}'.format(json.dumps(payload, indent=2)))
            r = requests.put(url, auth=(self._username, self._password), data=json.dumps(payload), verify=self._verify_ssl)
        elif req_type == 'delete':
            logging.info('Delete call to Pulp URL "{0}"'.format(url))
            r = requests.delete(url, auth=(self._username, self._password), verify=self._verify_ssl)
        else:
            raise ValueError('Invalid value of "req_type" parameter: {0}'.format(req_type))
        r_json = r.json()
        # some requests return null
        if not r_json:
            return r_json

        logging.debug('Pulp HTTP status code: {0}'.format(r.status_code))
        logging.debug('Pulp JSON response:\n{0}'.format(json.dumps(r_json, indent=2)))

        if 'error_message' in r_json:
            logging.warn('Error messages from Pulp response:\n{0}'.format(r_json['error_message']))

        if 'spawned_tasks' in r_json:
            for task in r_json['spawned_tasks']:
                logging.debug('Checking status of spawned task {0}'.format(task['task_id']))
                self._call_pulp('{0}/{1}'.format(self._server_url, task['_href']))
        return r_json

    @property
    def status(self):
        """Return pulp server status"""
        logging.info('Verifying Pulp server status')
        return self._call_pulp('{0}/pulp/api/v2/status/'.format(self._server_url))

    def verify_repo(self, repo_id):
        """Verify pulp repository exists"""
        url = '{0}/pulp/api/v2/repositories/{1}/'.format(self._server_url, repo_id)
        logging.info('Verifying pulp repository "{0}"'.format(repo_id))
        r_json = self._call_pulp(url)
        if 'error_message' in r_json:
            raise Exception('Repository "{0}" not found'.format(repo_id))

    def is_repo(self, repo_id):
        """Return true if repo exists"""
        url = '{0}/pulp/api/v2/repositories/'.format(self._server_url)
        logging.info('Verifying pulp repository "{0}"'.format(repo_id))
        r_json = self._call_pulp(url)
        return repo_id in [repo['id'] for repo in r_json]

    def create_repo(self, isv, image, repo_id):
        """Create pulp docker repository"""
        payload = {
        'id': repo_id,
        'display_name': '%s %s' % (isv, image),
        'description': 'docker image repository for ISV %s' % isv,
        'notes': {
            '_repo-type': 'docker-repo'
        },
        'importer_type_id': self._importer,
        'importer_config': {},
        'distributors': [{
            'distributor_type_id': 'docker_distributor_web',
            'distributor_id': self._web_distributor,
            'repo-registry-id': image,
            'auto_publish': 'true'},
            {
            'distributor_type_id': 'docker_distributor_export',
            'distributor_id': self._export_distributor,
            'repo-registry-id': image,
            'docker_publish_directory': self._export_dir,
            'auto_publish': 'true'}
            ]
        }
        url = '{0}/pulp/api/v2/repositories/'.format(self._server_url)
        logging.info('Verifying pulp repository "{0}"'.format(repo_id))
        r_json = self._call_pulp(url, "post", payload)
        if 'error_message' in r_json:
            raise Exception('Failed to create repository "{0}"'.format(repo_id))

    def update_redirect_url(self, repo_id, redirect_url):
        """Update distributor redirect URL and export file"""
        url = '{0}/pulp/api/v2/repositories/{1}/distributors/{2}/'.format(self._server_url, repo_id, self._export_distributor)
        payload = {
          "distributor_config": {
            "redirect-url": redirect_url
          }
        }
        logging.info('Update pulp repository "{0}" URL "{1}"'.format(repo_id, redirect_url))
        r_json = self._call_pulp(url, "put", payload)
        if 'error_message' in r_json:
            raise Exception('Unable to update pulp repo "{0}"'.format(repo_id))

    @property
    def _upload_id(self):
        """Get a pulp upload ID"""
        url = '{0}/pulp/api/v2/content/uploads/'.format(self._server_url)
        r_json = self._call_pulp(url, "post")
        if 'error_message' in r_json:
            raise Exception('Unable to get a pulp upload ID')
        return r_json['upload_id']

    def _delete_upload_id(self, upload_id):
        """Delete upload request ID"""
        logging.info('Deleting pulp upload ID {0}'.format(upload_id))
        url = '{0}/pulp/api/v2/content/uploads/{1}/'.format(self._server_url, upload_id)
        self._call_pulp(url, "delete")

    def upload_image(self, repo_id, file_upload):
        """Upload image to pulp repository"""
        if not os.path.isfile(file_upload):
            raise Exception('Cannot find file "{0}"'.format(file_upload))
        else:
            upload_id = self._upload_id
            logging.info('Uploading image using ID "{0}"'.format(upload_id))
            self._upload_bits(upload_id, file_upload)
            self._import_upload()
            self._delete_upload_id(upload_id)

    def _upload_bits(self, upload_id, file_upload):
        logging.info('Uploading file ({0})'.format(file_upload))
        offset = 0
        source_file_size = os.path.getsize(file_upload)
        f = open(file_upload, 'r')
        while True:
            f.seek(offset)
            data = f.read(self._chunk_size)
            if not data:
                break
            url = '{0}/pulp/api/v2/content/uploads/{1}/{2}/'.format(self._server_url, upload_id, offset)
            logging.info('Uploading {0}: {1} of {2} bytes'.format(file_upload, offset, source_file_size))
            #FIXME: broken call
            # ERROR:root:Failed to upload image to Pulp: 'utf8' codec can't decode byte 0xc8 in position 41475: invalid continuation byte
            #self._call_pulp(url, "put", data)
            offset = min(offset + self._chunk_size, source_file_size)
        f.close()

    def _import_upload(self, upload_id, repo_id):
        """Import uploaded content"""
        logging.info('Importing pulp upload {0} into {1}'.format(upload_id, repo_id))
        url = '{0}/pulp/api/v2/repositories/{1}/actions/import_upload/'.format(self._server_url, repo_id)
        payload = {
          'upload_id': upload_id,
          'unit_type_id': self._unit_type_id,
          'unit_key': None,
          'unit_metadata': None,
          'override_config': None
        }
        r_json = self._call_pulp(url, "post", payload)
        if 'error_message' in r_json:
            raise Exception('Unable to import pulp content into {0}'.format(repo_id))

    def export_repo(self, repo_id):
        """Export pulp repository"""
        url = '{0}/pulp/api/v2/repositories/{1}/actions/publish/'.format(self._server_url, repo_id)
        payload = {
          "id": self._export_distributor,
          "override_config": {
            "export_file": '{0}{1}.tar'.format(self._export_dir, repo_id),
          }
        }
        logging.info('Publishing pulp repository "{0}"'.format(repo_id))
        r_json = self._call_pulp(url, "post", payload)
        if 'error_message' in r_json:
            raise Exception('Unable to publish pulp repo "{0}"'.format(repo_id))

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


class RedHatMeta(object):
    """Information on Red Hat docker images"""

    def __init__(self, git_repo_url, relpath):
        self._image_ids = set()
        self._repo_url = git_repo_url
        self._relpath = relpath

    @property
    def image_ids(self):
        if not self._image_ids:
            tmpdir = mkdtemp()
            Repo.clone_from(self._repo_url, tmpdir)
            logging.info('Red Hat meta git cloned to "{0}"'.format(tmpdir))
            for filename in glob(os.path.join(tmpdir, self._relpath) + os.sep + '*.json'):
                logging.debug('Reading Red Hat meta file "{0}"'.format(filename))
                with open(filename) as f:
                    data = json.load(f)
                    logging.debug('Red Hat meta file "{0}" data:\n{1}'.format(filename, json.dumps(data, indent=2)))
                    for i in data['images']:
                        self._image_ids.add(i['id'])
            logging.info('Red Hat image IDs: {0}'.format(self._image_ids))
            rmtree(tmpdir)
        return self._image_ids


class AwsS3(object):
    """Interact with AWS S3"""

    def __init__(self, bucket_name, app_name):
        self._bucket = None
        self._image_ids = set()
        self._bucket_name = bucket_name
        self._app_name = app_name
        self._connect()
        #self.bucket = kwargs['bucket_name']
        #self.app = kwargs['app_name']
        #self.images_dir = kwargs['images_dir']
        #self.mask_layers = kwargs['mask_layers']

    @property
    def bucket_name(self):
        return self._bucket_name

    @property
    def bucket(self):
        if not self._bucket:
            self._bucket = self._conn.get_bucket(self._bucket_name)
        return self._bucket

    @property
    def image_ids(self):
        if not self._image_ids:
            for i in self.bucket.list(prefix=self._app_name + '/', delimiter='/'):
                self._image_ids.add(i.name.split('/')[1])
            logging.info('AWS image ids: {0}'.format(self._image_ids))
        return self._image_ids

    def _connect(self):
        logging.info('Connecting to AWS')
        self._conn = connect_s3()

    def verify_bucket(self):
        logging.info('Looking up bucket "{0}"'.format(self._bucket_name))
        if not self._conn.lookup(self._bucket_name):
            raise Exception('Bucket "{0}" not found'.format(self._bucket_name))

    def status(self):
        result = True
        logging.info('Checking AWS status...')
        try:
            self.verify_bucket()
            print 'AWS bucket "{0}" looks OK'.format(self.bucket_name)
        except Exception as e:
            logging.error('Failed to verify AWS bucket: {0}'.format(e))
            result = False
        return result

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

    def __init__(self, server_url, username, password, domain, app_name, isv_app_name):
        self._app_data = None
        self._app_local_dir = None
        self._app_repo = None
        self._image_ids = set()
        self._server_url = server_url
        self._username = username
        self._password = password
        self._domain = domain
        self._app_name = app_name
        self._isv_app_name = isv_app_name
        #self.app_git_url = kwargs['app_git_url']
        #self.cartridge = kwargs['cartridge']
        # FIXME:
        #self.cranefile = cranefile

    @property
    def domain(self):
        return self._domain

    @property
    def app_name(self):
        return self._app_name

    @property
    def app_local_dir(self):
        if not self._app_local_dir:
            self._app_local_dir = mkdtemp()
            logging.info('Created local Openshift app dir "{0}"'.format(self._app_local_dir))
        return self._app_local_dir

    @property
    def app_data(self):
        if not self._app_data:
            url = '{0}/broker/rest/domain/{1}/applications'.format(self._server_url, self.domain)
            logging.info('Getting Openshift app data for "{0}"'.format(self.app_name))
            r_json = self._call_openshift(url)
            if r_json['status'] != 'ok':
                raise Exception('Failed to get applications in domain "{0}"'.format(self.domain))
            for app in r_json['data']:
                logging.debug('Inspecting Openshift app "{0}" with ID "{1}"'.format(app['name'], app['id']))
                if app['name'] == self.app_name:
                    logging.info('Found Openshift app "{0}" with ID "{1}"'.format(self.app_name, app['id']))
                    self._app_data = app
                    break
            else:
                raise Exception('Application "{0}" not found in domain "{1}"'.format(self.app_name, self.domain))
        return self._app_data

    @property
    def image_ids(self):
        if not self._image_ids:
            with open(self._isv_app_crane_file) as f:
                data = json.load(f)
            logging.debug('Crane "{0}.json" data:\n{1}'.format(self._isv_app_name, json.dumps(data, indent=2)))
            self._image_ids = [i['id'] for i in data['images']]
            self._image_ids = set(self._image_ids)
            logging.info('Crane image IDs: {0}'.format(self._image_ids))
        return self._image_ids

    @property
    def _isv_app_crane_file(self):
        self.clone_app()
        filename = os.path.join(self.app_local_dir, 'crane', 'data', self._isv_app_name + '.json')
        if not os.path.isfile(filename):
            raise Exception('ISV app crane file "{0}" does not exist'.format(filename))
        return filename

    @property
    def _env_vars(self):
        """Required environment variables to make crane work on openshift"""
        return [('OPENSHIFT_PYTHON_WSGI_APPLICATION', 'crane/wsgi.py'),
                ('OPENSHIFT_PYTHON_DOCUMENT_ROOT', 'crane/')]

    def _call_openshift(self, url, req_type='get', payload=None):
        if req_type == 'get':
            logging.info('Calling Openshift URL "{0}"'.format(url))
            r = requests.get(url, auth=(self._username, self._password))
        elif req_type == 'post':
            logging.info('Posting to Openshift URL "{0}"'.format(url))
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
            logging.info('Setting environment variable "{0}"'.format(var[0]))
            payload = {'name': var[0],
                       'value': var[1]}
            self._call_openshift(url, 'post', payload)

    def clone_app(self):
        if not self._app_repo:
            logging.info('Clonning Openshift app "{0}"'.format(self.app_name))
            self._app_repo = Repo.clone_from(self.app_data['git_url'], self.app_local_dir)

    def create_app(self):
        """Create an Openshift application"""
        payload = {'name': self.app_name,
                   'cartridge': self.cartridge,
                   #'scale': True,
                   'initial_git_url': self.app_git_url}
        url = self._server_url + '/broker/rest/domains/' + self.domain + '/applications'
        logging.info('Creating OpenShift application')
        text = self._call_openshift(url, 'post', payload)
        logging.info('Created app "{0}"'.format(self.app_name))
        #self.app_id = r.text['data']['id']
        #print json.dumps(r.json(), indent=4)
        self.app_data = text['data']
        self._set_env_vars(text['data']['links']['ADD_ENVIRONMENT_VARIABLE']['href'])
        self._restart_app()

    def verify_domain(self):
        """Verify that Openshift domain exists"""
        url = '{0}/broker/rest/domains/{1}'.format(self._server_url, self.domain)
        logging.info('Verifying Openshift domain "{0}"'.format(self.domain))
        r_json = self._call_openshift(url)
        if r_json['status'] != 'ok':
            raise Exception('Domain "{0}" not found'.format(self.domain))

    def verify_app(self):
        url = self.app_data['app_url'] + 'v1/_ping'
        logging.info('Verifying Crane app status on url "{0}"'.format(url))
        r = requests.get(url)
        logging.debug('Crane app HTTP status code: {0}'.format(r.status_code))
        logging.debug('Crane app response: {0}'.format(r.text))
        if r.status_code != 200:
            raise Exception('Crane ping HTTP status code is not "200" but: {0}'.format(r.status_code))
        if r.text != 'true':
            raise Exception('Crane ping response is not "true" but: {0}'.format(r.text))

    def status(self):
        result = True
        try:
            self.verify_domain()
            print 'Openshift domain "{0}" looks OK'.format(self.domain)
            self.verify_app()
            print 'Openshift Crane app on "{0}" looks alive'.format(self.app_data['app_url'])
            self.clone_app()
            print 'Cloned Openshift app "{0}" to "{1}"'.format(self.app_name, self.app_local_dir)
            cranefile = self._isv_app_crane_file
            print 'ISV app crane file "{0}" exists'.format(cranefile)
        except Exception as e:
            logging.error('Failed to verify Openshift status: {0}'.format(e))
            result = False
        return result

    def cleanup(self):
        if self._app_local_dir:
            logging.info('Removing local Openshift app dir "{0}"'.format(self._app_local_dir))
            rmtree(self._app_local_dir)
            self._app_local_dir = None


class Configuration(object):
    """Configuration and utilities"""

    _CONFIG_FILE_NAME = 'raas.cfg'
    _CONFIG_REPO_ENV_VAR = 'RAAS_CONF_REPO'
    _S3_URL = "https://s3.amazonaws.com"

    def __init__(self, isv, isv_app_name=None, image=None, file_upload=None):
        """Setup Configuration object.

        Use current working dir as local config if it exists,
        otherwise clone repo based on RAAS_CONF_REPO env var.
        """
        self._pulp_repo = None
        self.isv = isv
        self._isv_app_name = isv_app_name
        if image:
            self.pulp_repo = image
        elif file_upload:
            if not ".tar" in file_upload:
                raise Exception('Uploaded file must be output of "docker save some/image > some-image.tar"')
            else:
                strip_tar = re.sub('\.tar$', '', file_upload)
                self.pulp_repo = strip_tar
                self.image = strip_tar.replace('-', '/')

        if os.path.isfile(self._CONFIG_FILE_NAME):
            self._conf_dir = os.getcwd()
            logging.info('Using configuration in current dir "{0}"'.format(self._conf_dir))
        else:
            repo_url = os.getenv(self._CONFIG_REPO_ENV_VAR)
            if not repo_url:
                raise Exception('Current working directory does not contain "{0}" configuration file ' + \
                        'and environment variable "{1}" is not set.'.format(self._CONFIG_FILE_NAME, self._CONFIG_REPO_ENV_VAR))
            self._conf_dir = mkdtemp()
            logging.info('Clonning config repo from "{0}" to "{1}"'.format(repo_url, self._conf_dir))
            self._config_repo = Repo.clone_from(repo_url, self._conf_dir)

        self._conf_file = os.path.join(self._conf_dir, self._CONFIG_FILE_NAME)
        if not os.path.isfile(self._conf_file):
            raise Exception('Configuration file "{0}" not found'.format(self._conf_file))
        self._parsed_config = ConfigParser()
        self._parsed_config.read(self._conf_file)

        logging.info('Using conf file "{0}"'.format(self._conf_file))

        self._setup_isv_config_dirs()
        self._setup_isv_config_file()

    @property
    def isv(self):
        return self._isv

    @isv.setter
    def isv(self, val):
        if not val.isalnum():
            raise ValueError('ISV "{0}" must contain only alphanumeric characters'.format(val))
        self._isv = val.lower()
        logging.debug('ISV set to "{0}"'.format(self.isv))

    @property
    def isv_app_name(self):
        return self._isv_app_name

    @property
    def pulp_repo(self):
        """Pulp-friendly repository name with ISV name and without slash"""
        return self._pulp_repo

    @pulp_repo.setter
    def pulp_repo(self, image):
        img_replace = image.replace('/', '-')
        self._pulp_repo = '-'.join([self.isv, img_replace])

    @property
    def pulp_redirect_url(self):
        """Returns Pulp server redirect URL for S3 bucket"""
        return '/'.join([self._S3_URL,
                         self._parsed_config.get(self.isv, 's3_bucket'),
                         self._isv_app_name])

    @property
    def pulp_conf(self):
        return {'server_url': self._parsed_config.get('pulpserver', 'host'),
                'username'  : self._parsed_config.get('pulpserver', 'username'),
                'password'  : self._parsed_config.get('pulpserver', 'password'),
                'verify_ssl': self._parsed_config.getboolean('pulpserver', 'verify_ssl')}

    @property
    def openshift_conf(self):
        return {'server_url'  : self._parsed_config.get('openshift', 'server_url'),
                'username'    : self._parsed_config.get('openshift', 'username'),
                'password'    : self._parsed_config.get('openshift', 'password'),
                'domain'      : self._parsed_config.get(self.isv, 'openshift_domain'),
                'app_name'    : self._parsed_config.get(self.isv, 'openshift_app'),
                'isv_app_name': self._isv_app_name}

    @property
    def aws_conf(self):
        return {'bucket_name': self._parsed_config.get(self.isv, 's3_bucket'),
                'app_name'   : self._isv_app_name}

    @property
    def redhat_meta_conf(self):
        return {'git_repo_url': self._parsed_config.get('redhat', 'metadata_repo'),
                'relpath'     : self._parsed_config.get('redhat', 'metadata_relpath')}

    def commit_all_changes(self):
        #self._config_repo._index.add(FIXME)
        #self._config_repo._index.commit(FIXME)
        #self._config_repo.remotes.origin.push()
        raise NotImplemented()

    def _setup_isv_config_dirs(self):
        logdir = os.path.join(self._conf_dir, self.isv, 'logs')
        metadir = os.path.join(self._conf_dir, self.isv, 'metadata')
        if not os.path.exists(logdir):
            logging.info('Creating log dir "{0}"'.format(logdir))
            os.makedirs(logdir)
        if not os.path.exists(metadir):
            logging.info('Creating metadata dir "{0}"'.format(metadir))
            os.makedirs(metadir)

    def _setup_isv_config_file(self):
        """Setup config file defaults if not provided"""
        if not self._parsed_config.has_section(self.isv):
            logging.info('Creating default ISV section in config file')
            self._parsed_config.add_section(self.isv)
            self._parsed_config.set(self.isv, 'openshift_domain', self.isv)
            self._parsed_config.set(self.isv, 'openshift_app', 'registry')
            self._parsed_config.set(self.isv, 's3_bucket', self.isv + '.bucket')
            with open(self._conf_file, 'w') as configfile:
                self._parsed_config.write(configfile)


def main():
    """Entrypoint for script"""
    isv_args = ['isv']
    isv_kwargs = {'metavar': 'ISV_NAME',
                  'help': 'ISV name matching config file and OpenShift Online domain'}
    isv_app_args = ['isv_app']
    isv_app_kwargs = {'metavar': 'ISV_APP_NAME',
                      'help': 'ISV Application name'}
    parser = ArgumentParser()
    parser.add_argument('-l', '--log', metavar='LOG_LEVEL',
            help='Desired log level. Can be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL. Default is WARNING.')
    parser.add_argument('-n', '--nocommit', action='store_true',
                        help='Do not commit configuration. Development only.')
    subparsers = parser.add_subparsers(help='sub-command help', dest='action')
    status_parser = subparsers.add_parser('status', help='Check configuration status')
    status_parser.add_argument(*isv_args, **isv_kwargs)
    status_parser.add_argument(*isv_app_args, **isv_app_kwargs)
    setup_parser = subparsers.add_parser('setup', help='Setup initial configuration')
    setup_parser.add_argument(*isv_args, **isv_kwargs)
    setup_parser.add_argument('-f', '--file_upload', metavar='IMAGE-NAME.TAR',
            help='File to upload to pulp server. Output of of "docker save some/image > image-name.tar". This does not setup rest of the environment.')
    push_parser = subparsers.add_parser('push', help='Push or update an image')
    push_parser.add_argument(*isv_args, **isv_kwargs)
    push_parser.add_argument('image', metavar='IMAGE', help='Image name')
    args = parser.parse_args()

    if args.log:
        log_level = getattr(logging, args.log.upper(), None)
        if isinstance(log_level, int):
            logging.basicConfig(level=log_level)

    try:
        config_kwargs = {}
        if hasattr(args, 'isv_app'):
            config_kwargs['isv_app_name'] = args.isv_app
        if hasattr(args, 'image'):
            config_kwargs['image'] = args.image
        if hasattr(args, 'file_upload'):
            config_kwargs['file_upload'] = args.file_upload
        config = Configuration(args.isv, **config_kwargs)
    except Exception as e:
        logging.critical('Failed to initialize raas: {0}'.format(e))
        sys.exit(1)

    try:
        openshift = Openshift(**config.openshift_conf)
    except Exception as e:
        logging.critical('Failed to initialize Openshift: {0}'.format(e))
        sys.exit(1)

    try:
        aws = AwsS3(**config.aws_conf)
    except Exception as e:
        logging.critical('Failed to initialize AWS: {0}'.format(e))
        sys.exit(1)

    if args.action in 'status':
        status = True
        if not aws.status():
            status = False
        if not openshift.status():
            status = False
        if status:
            try:
                if openshift.image_ids == aws.image_ids:
                    print 'Openshift Crane images matches AWS images'
                else:
                    logging.error('Openshift Crane images does not match AWS images:\nCrane: {0}\nAWS: {1}'.format(openshift.image_ids, aws.image_ids))
                    status = False
            except Exception as e:
                logging.error('Failed to compare Openshift and AWS images: {0}'.format(e))
        if status:
            print 'Status of "{0}" should be OK'.format(config.isv)
        else:
            print 'Failed to verify status of "{0}"'.format(config.isv)

    elif args.action in 'setup':
        if args.file_upload:
            # special case to create repo if not exists and upload file to pulp server
            try:
                pulp = PulpServer(**config.pulp_conf)
                pulp.status
            except Exception as e:
                logging.critical('Failed to initialize Pulp: {0}'.format(e))
                sys.exit(1)
            if not pulp.is_repo(config.pulp_repo):
                try:
                    pulp.create_repo(config.isv, config.image, config.pulp_repo)
                except Exception as e:
                    logging.critical('Failed to create Pulp repository: {0}'.format(e))
                    sys.exit(1)
            else:
                logging.info('Pulp repository "{0}" already exists'.format(config.pulp_repo))
            try:
                pulp.upload_image(config.pulp_repo, args.file_upload)
                print 'Uploaded image to pulp repo "{0}"'.format(config.pulp_repo)
            except Exception as e:
                logging.error('Failed to upload image to Pulp: {0}'.format(e))
                sys.exit(1)

            sys.exit(1)

    elif args.action in 'push':
        try:
            pulp = PulpServer(**config.pulp_conf)
            pulp.status
        except Exception as e:
            logging.critical('Failed to initialize Pulp: {0}'.format(e))
            sys.exit(1)
        try:
            pulp.verify_repo(config.pulp_repo)
            print 'Pulp repo "{0}" looks OK'.format(config.pulp_repo)
        except Exception as e:
            logging.error('Failed to verify pulp repository: {0}'.format(e))
            sys.exit(1)
        try:
            pulp.update_redirect_url(config.pulp_repo, config.pulp_redirect_url)
            print 'Update pulp redirect URL for repo "{0}"'.format(config.pulp_repo)
        except Exception as e:
            logging.error('Failed to update pulp repository: {0}'.format(e))
            sys.exit(1)
        try:
            pulp.export_repo(config.pulp_repo)
            print 'Exporting pulp repo "{0}"'.format(config.pulp_repo)
        except Exception as e:
            logging.error('Failed to export pulp repository: {0}'.format(e))
            sys.exit(1)

        #mask_layers = conf_file.get('redhat', 'mask_layers')
        #mask_layers = re.split(',| |\n', mask_layers.strip())
        #pulptar = PulpTar(args.tarfile)
        #pulptar.get_tarfile()
        #cranefile = pulptar.crane_metadata_file
        #kwargs = {'bucket_name': args.bucket_name,
        #          'app_name': args.app_name,
        #          'images_dir': pulptar.docker_images_dir,
        #          'mask_layers': mask_layers}
        #s3 = AwsS3(**kwargs)
        #files = s3.walk_dir(pulptar.docker_images_dir)
        #s3.upload_layers(files)
        #os = Openshift(**conf_file._sections['openshift'])
        #os.create_app()

    if not args.nocommit:
        config.commit_all_changes()

    openshift.cleanup()


if __name__ == '__main__':
    main()
