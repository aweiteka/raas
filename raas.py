#!/usr/bin/env python

import json
import logging
import os
import re
import requests
import shutil
import sys
import tarfile

from argparse import ArgumentParser
from boto import connect_s3
from boto import s3
from ConfigParser import ConfigParser
from datetime import date
from git import Repo
from glob import glob
from tempfile import mkdtemp
from time import sleep


class PulpServer(object):
    """Interact with Pulp API"""

    def __init__(self, server_url, username, password, verify_ssl, isv,
                 isv_app_name, redirect_url):
        self._upload_id = None
        self._repo_id = None
        self._data_dir = None
        self._exported_local_file = None
        self.server_url = server_url
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._isv = isv
        self._isv_app_name = isv_app_name
        self._redirect_url = redirect_url
        self._web_distributor = 'docker_web_distributor_name_cli'
        self._export_distributor = 'docker_export_distributor_name_cli'
        self._importer = 'docker_importer'
        self._export_dir = '/var/www/pub/docker/web/'
        self._unit_type_id = 'docker_image'
        self._chunk_size = 1048576 # 1 MB per upload call

    @property
    def server_url(self):
        return self._server_url

    @server_url.setter
    def server_url(self, val):
        if val.startswith('https://'):
            self._server_url = val
        else:
            self._server_url = 'https://' + val

    @property
    def data_dir(self):
        if not self._data_dir:
            self._data_dir = mkdtemp()
            logging.info('Created pulp data dir "{0}"'.format(self._data_dir))
        return self._data_dir

    @property
    def repo_id(self):
        if not self._repo_id and self._isv_app_name:
            self._repo_id = '-'.join([self._isv, self._isv_app_name])
        return self._repo_id

    @property
    def exported_local_file(self):
        if not self._exported_local_file:
            self._exported_local_file = os.path.join(self.data_dir, self.repo_id + '.tar')
        return self._exported_local_file

    @property
    def crane_config_file(self):
        return os.path.join(self.data_dir, self.repo_id + '.json')

    @property
    def upload_id(self):
        """Get a pulp upload ID"""
        if not self._upload_id:
            url = '{0}/pulp/api/v2/content/uploads/'.format(self.server_url)
            r_json = self._call_pulp(url, 'post')
            if 'error_message' in r_json:
                raise Exception('Unable to get a pulp upload ID')
            self._upload_id = r_json['upload_id']
            logging.info('Received pulp upload ID: {0}'.format(self._upload_id))
        return self._upload_id

    def _call_pulp(self, url, req_type='get', payload=None, return_json=True, p_stream=False):
        if req_type == 'get':
            logging.info('Calling Pulp URL "{0}"'.format(url))
            r = requests.get(url, auth=(self._username, self._password), verify=self._verify_ssl, stream=p_stream)
        elif req_type == 'post':
            logging.info('Posting to Pulp URL "{0}"'.format(url))
            if payload:
                logging.debug('Pulp HTTP payload:\n{0}'.format(json.dumps(payload, indent=2)))
            r = requests.post(url, auth=(self._username, self._password), data=json.dumps(payload), verify=self._verify_ssl)
        elif req_type == 'put':
            # some calls pass in binary data so we don't log payload data or json encode it here
            logging.info('Putting to Pulp URL "{0}"'.format(url))
            r = requests.put(url, auth=(self._username, self._password), data=payload, verify=self._verify_ssl)
        elif req_type == 'delete':
            logging.info('Delete call to Pulp URL "{0}"'.format(url))
            r = requests.delete(url, auth=(self._username, self._password), verify=self._verify_ssl)
        else:
            raise ValueError('Invalid value of "req_type" parameter: {0}'.format(req_type))

        logging.debug('Pulp HTTP status code: {0}'.format(r.status_code))

        if r.status_code >= 400:
            raise Exception('Received invalid status code: {0}'.format(r.status_code))

        if return_json:
            r_json = r.json()
            # some requests return null
            if not r_json:
                return r_json
            logging.debug('Pulp JSON response:\n{0}'.format(json.dumps(r_json, indent=2)))

            if 'error_message' in r_json:
                logging.warn('Error messages from Pulp response:\n{0}'.format(r_json['error_message']))

            if 'spawned_tasks' in r_json:
                for task in r_json['spawned_tasks']:
                    self._watch_task(task['task_id'], task['_href'])
            return r_json
        else:
            return r

    def _watch_task(self, tid, thref, timeout=60, poll=5):
        """Watch a task ID and return when it finishes or fails"""
        logging.info('Waiting up to "{0}" seconds for task "{1}"...'.format(timeout, tid))
        curr = 0
        while curr < timeout:
            t = self._call_pulp('{0}{1}'.format(self.server_url, thref))
            if t['state'] == 'finished':
                logging.info('Subtask "{0}" completed'.format(tid))
                return True
            elif t['state'] == 'error':
                logging.error('Subtask "{0}" had an error: {1}'.format(tid, t['error']))
                logging.debug('Traceback from subtask "{0}":\n{1}'.format(tid, t['traceback']))
                raise Exception('Pulp task "{0}" failed with error: {1}'.format(tid, t['error']))
            else:
                logging.debug('Waiting for task "{0}" ({1}/{2} seconds passed)'.format(tid, curr, timeout))
                sleep(poll)
                curr += poll
        logging.error('Timed out waiting for pulp task "{0}"'.format(tid))
        raise Exception('Timed out waiting for pulp task "{0}"'.format(tid))

    def status(self):
        """Check pulp server status"""
        logging.info('Verifying Pulp server status..')
        self._call_pulp('{0}/pulp/api/v2/status/'.format(self.server_url))
        print 'Pulp server looks OK'

    def verify_repo(self):
        """Verify pulp repository exists"""
        url = '{0}/pulp/api/v2/repositories/{1}/'.format(self.server_url, self.repo_id)
        logging.info('Verifying pulp repository "{0}"'.format(self.repo_id))
        r_json = self._call_pulp(url)
        if 'error_message' in r_json:
            raise Exception('Repository "{0}" not found'.format(self.repo_id))
        print 'Pulp repository looks OK'

    def is_repo(self):
        """Return true if repo exists"""
        url = '{0}/pulp/api/v2/repositories/'.format(self.server_url)
        logging.info('Verifying pulp repository "{0}"'.format(self.repo_id))
        r_json = self._call_pulp(url)
        return self.repo_id in [repo['id'] for repo in r_json]

    def create_repo(self):
        """Create pulp docker repository"""
        payload = {
        'id': self.repo_id,
        'display_name': '{0} {1}'.format(self._isv, self._isv_app_name),
        'description': 'docker image repository for ISV {0}'.format(self._isv),
        'notes': {
            '_repo-type': 'docker-repo'
        },
        'importer_type_id': self._importer,
        'importer_config': {},
        'distributors': [{
            'distributor_type_id': 'docker_distributor_web',
            'distributor_id': self._web_distributor,
            'config': {
                'repo-registry-id': self._isv_app_name},
            'auto_publish': 'true'},
            {
            'distributor_type_id': 'docker_distributor_export',
            'distributor_id': self._export_distributor,
            'config': {
                'repo-registry-id': self._isv_app_name},
            'docker_publish_directory': self._export_dir,
            'auto_publish': 'true'}
            ]
        }
        url = '{0}/pulp/api/v2/repositories/'.format(self.server_url)
        logging.info('Verifying pulp repository "{0}"'.format(self.repo_id))
        r_json = self._call_pulp(url, "post", payload)
        if 'error_message' in r_json:
            raise Exception('Failed to create repository "{0}"'.format(self.repo_id))

    def update_redirect_url(self):
        """Update distributor redirect URL and export file"""
        url = '{0}/pulp/api/v2/repositories/{1}/'.format(self.server_url, self.repo_id)
        payload = {
                'distributor_configs': {
                        self._export_distributor: {
                            'redirect-url': self._redirect_url
                        }
                }
        }
        logging.info('Update pulp repository "{0}" URL "{1}"'.format(self.repo_id, self._redirect_url))
        r_json = self._call_pulp(url, 'put', json.dumps(payload))
        if 'error_message' in r_json:
            raise Exception('Unable to update pulp repo "{0}"'.format(self.repo_id))
        print 'Updated pulp redirect URL for repo "{0}"'.format(self.repo_id)

    def _delete_upload_id(self):
        """Delete upload request ID"""
        logging.info('Deleting pulp upload ID {0}'.format(self.upload_id))
        url = '{0}/pulp/api/v2/content/uploads/{1}/'.format(self.server_url, self.upload_id)
        self._call_pulp(url, 'delete')
        self._upload_id = None

    def upload_image(self, file_upload):
        """Upload image to pulp repository"""
        if os.path.isfile(file_upload):
            self._upload_bits(file_upload)
            self._import_upload()
            self._delete_upload_id()
            self._publish_repo()
        else:
            raise Exception('Cannot find file "{0}"'.format(file_upload))

    def _upload_bits(self, file_upload):
        logging.info('Uploading file "{0}"'.format(file_upload))
        offset = 0
        source_file_size = os.path.getsize(file_upload)
        with open(file_upload, 'r') as f:
            while True:
                f.seek(offset)
                data = f.read(self._chunk_size)
                if not data:
                    break
                url = '{0}/pulp/api/v2/content/uploads/{1}/{2}/'.format(self.server_url, self.upload_id, offset)
                logging.info('Uploading "{0}": {1} of {2} bytes'.format(file_upload, offset, source_file_size))
                self._call_pulp(url, 'put', data)
                offset = min(offset + self._chunk_size, source_file_size)

    def _import_upload(self):
        """Import uploaded content"""
        logging.info('Importing pulp upload {0} into {1}'.format(self.upload_id, self.repo_id))
        url = '{0}/pulp/api/v2/repositories/{1}/actions/import_upload/'.format(self.server_url, self.repo_id)
        payload = {
          'upload_id': self.upload_id,
          'unit_type_id': self._unit_type_id,
          'unit_key': None,
          'unit_metadata': None,
          'override_config': None
        }
        r_json = self._call_pulp(url, 'post', payload)
        if 'error_message' in r_json:
            raise Exception('Unable to import pulp content into {0}'.format(self.repo_id))

    def _publish_repo(self):
        """Publish pulp repository to pulp web server"""
        url = '{0}/pulp/api/v2/repositories/{1}/actions/publish/'.format(self.server_url, self.repo_id)
        payload = {
          'id': self._web_distributor,
          'override_config': {}
        }
        logging.info('Publishing pulp repository "{0}"'.format(self.repo_id))
        r_json = self._call_pulp(url, 'post', payload)
        if 'error_message' in r_json:
            raise Exception('Unable to publish pulp repo "{0}"'.format(self.repo_id))

    def export_repo(self):
        """Export pulp repository to pulp web server as tar

        The tarball is split into the layer components and crane metadata.
        It is for the purpose of uploading to remote crane server"""
        url = '{0}/pulp/api/v2/repositories/{1}/actions/publish/'.format(self.server_url, self.repo_id)
        payload = {
          'id': self._export_distributor,
          'override_config': {
            'export_file': '{0}{1}.tar'.format(self._export_dir, self.repo_id),
          }
        }
        logging.info('Exporting pulp repository "{0}"'.format(self.repo_id))
        r_json = self._call_pulp(url, 'post', payload)
        if 'error_message' in r_json:
            raise Exception('Unable to export pulp repo "{0}"'.format(self.repo_id))
        print 'Exported pulp repo "{0}"'.format(self.repo_id)

    def remove_orphan_content(self, content_type='docker_image'):
        """Remove orphan content"""
        if self._list_orphans():
            logging.info('Removing orphaned content "{0}"'.format(content_type))
            url = '{0}/pulp/api/v2/content/orphans/{1}/'.format(self.server_url, content_type)
            r_json = self._call_pulp(url, 'delete')
            if 'error_message' in r_json:
                raise Exception('Unable to remove orphaned content type "{0}"'.format(content_type))

    def _list_orphans(self, content_type='docker_image'):
        """List (log) orphan content. Defaults to docker content"""
        url = '{0}/pulp/api/v2/content/orphans/{1}/'.format(self.server_url, content_type)
        r_json = self._call_pulp(url)
        content = [content['image_id'] for content in r_json]
        logging.info('Orphan "{0}" content:\n{1}'.format(content_type, content))
        if 'error_message' in r_json:
            raise Exception('Unable to list orphaned content type "{0}"'.format(content_type))
        return content

    def download_repo(self):
        url = '{0}/pulp/docker/{1}.tar'.format(self.server_url, self.repo_id)
        r = self._call_pulp(url, 'get', return_json=False, p_stream=True)
        with open(self.exported_local_file, 'wb') as fd:
            for chunk in r.iter_content(self._chunk_size):
                fd.write(chunk)
        logging.info('Exported repo downloaded to "{0}"'.format(self.exported_local_file))
        with tarfile.open(self.exported_local_file) as tar:
            tar.extractall(self.data_dir)
        logging.info('Downloaded repo extracted to "{0}"'.format(self.data_dir))

    def files_for_aws(self, redhat_images):
        """Return list of tuples of files from pulp to be uploaded to aws.

        Format of returned list: [(layer_id/file1, full_file_path1), ...]
        """
        files = []
        # Walk the directory to get all the files to be uploaded
        for dirpath, _, filenames in os.walk(os.path.join(self.data_dir, 'web')):
            for filename in filenames:
                layer_id = os.path.basename(dirpath.rstrip(os.sep))
                if layer_id in redhat_images:
                    logging.info('Skipping Red Hat layer "{0}"'.format(layer_id))
                    continue
                fname = '/'.join([layer_id, filename])
                files.append((fname, os.path.join(dirpath, filename)))
                logging.debug('File "{0}" queued for upload to AWS'.format(fname))
        if not files:
            raise Exception('No files to upload to AWS')
        return files

    def cleanup(self):
        if self._data_dir:
            logging.info('Removing pulp data dir "{0}"'.format(self._data_dir))
            shutil.rmtree(self._data_dir)
            self._data_dir = None


class RedHatMeta(object):
    """Information on Red Hat docker images"""

    def __init__(self, git_repo_url, relpath):
        self._data_dir = None
        self._repo = None
        self._image_ids = set()
        self._repo_url = git_repo_url
        self._relpath = relpath

    @property
    def data_dir(self):
        if not self._data_dir:
            self._data_dir = mkdtemp()
            logging.info('Created Red Hat meta data dir "{0}"'.format(self._data_dir))
        return self._data_dir

    @property
    def redhat_meta_files(self):
        self._clone_repo()
        glob_path = os.path.join(self.data_dir, self._relpath) + os.sep + '*.json'
        logging.info('Looking for Red Hat meta files in "{0}"'.format(glob_path))
        rhmeta_files = glob(glob_path)
#         if not rhmeta_files:
#             raise Exception('No Red Hat meta files found')
        logging.debug('Found Red Hat meta files: {0}'.format(rhmeta_files))
        return rhmeta_files

    @property
    def image_ids(self):
        if not self._image_ids:
            for filename in self.redhat_meta_files:
                logging.debug('Reading Red Hat meta file "{0}"'.format(filename))
                with open(filename) as f:
                    data = json.load(f)
                    logging.debug('Red Hat meta file "{0}" data:\n{1}'.format(filename, json.dumps(data, indent=2)))
                    for i in data['images']:
                        self._image_ids.add(i['id'])
            logging.debug('Red Hat image IDs: {0}'.format(self._image_ids))
        return self._image_ids

    def _clone_repo(self):
        if not self._repo:
            self._repo = Repo.clone_from(self._repo_url, self.data_dir)
            logging.info('Red Hat meta git cloned to "{0}"'.format(self.data_dir))

    def cleanup(self):
        if self._data_dir:
            logging.info('Removing Red Hat meta data dir "{0}"'.format(self._data_dir))
            shutil.rmtree(self._data_dir)
            self._data_dir = None
            self._repo = None


class AwsS3(object):
    """Interact with AWS S3"""

    def __init__(self, bucket_name, app_name, aws_key, aws_secret):
        self._bucket = None
        self._image_ids = set()
        self._bucket_name = bucket_name
        self._app_name = app_name
        self._connect(aws_key, aws_secret)

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

    def _connect(self, aws_key, aws_secret):
        logging.info('Connecting to AWS')
        self._conn = connect_s3(aws_access_key_id=aws_key,
                                aws_secret_access_key=aws_secret)

    def verify_bucket(self):
        logging.info('Looking up bucket "{0}"'.format(self._bucket_name))
        if not self._conn.lookup(self._bucket_name):
            raise Exception('Bucket "{0}" not found'.format(self._bucket_name))

    def status(self):
        result = True
        logging.info('Checking AWS status..')
        try:
            self.verify_bucket()
            print 'AWS bucket "{0}" looks OK'.format(self.bucket_name)
        except Exception as e:
            logging.error('Failed to verify AWS bucket: {0}'.format(e))
            result = False
        return result

    def create_bucket(self):
        try:
            self.verify_bucket()
            logging.info('Bucket "{0}" already exists'.format(self.bucket_name))
        except Exception:
            logging.info('Creating bucket "{0}"'.format(self.bucket_name))
            self._bucket = self._conn.create_bucket(self.bucket_name)

    def upload_layers(self, files):
        """Upload image layers to S3 bucket"""
        logging.info('Uploading files to bucket "{0}"'.format(self._bucket_name))
        for name, path in files:
            with open(path, 'rb') as f:
                dest = '/'.join([self._app_name, name])
                key = s3.key.Key(bucket=self.bucket, name=dest)
                key.set_contents_from_file(f, replace=True)
                key.set_acl('public-read')
                logging.debug('Uploaded file "{0}"'.format(dest))
        logging.info('All files uploaded to AWS')


class Openshift(object):
    """Interact with Openshift REST API"""

    def __init__(self, server_url, token, domain, app_name,
                 app_git_url, cartridge, isv_app_name):
        self._app_data = None
        self._app_local_dir = None
        self._app_repo = None
        self._image_ids = set()
        self._server_url = server_url
        self._token = token
        self._domain = domain
        self._app_name = app_name
        self._app_git_url = app_git_url
        self._cartridge = cartridge
        self._isv_app_name = isv_app_name

    @property
    def domain(self):
        return self._domain

    @property
    def app_name(self):
        return self._app_name

    @property
    def isv_app_name(self):
        return self._isv_app_name

    @property
    def app_local_dir(self):
        if not self._app_local_dir:
            self._app_local_dir = mkdtemp()
            logging.info('Created local Openshift app dir "{0}"'.format(self._app_local_dir))
        return self._app_local_dir

    @property
    def app_data(self):
        if not self._app_data:
            url = 'broker/rest/domain/{0}/applications'.format(self.domain)
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
            logging.debug('Crane "{0}.json" data:\n{1}'.format(self.isv_app_name, json.dumps(data, indent=2)))
            self._image_ids = [i['id'] for i in data['images']]
            self._image_ids = set(self._image_ids)
            logging.info('Crane image IDs: {0}'.format(self._image_ids))
        return self._image_ids

    @property
    def _isv_app_crane_file(self):
        self.clone_app()
        filename = os.path.join(self.app_local_dir, 'crane', 'data', self.isv_app_name + '.json')
        if not os.path.isfile(filename):
            raise Exception('ISV app crane file "{0}" does not exist'.format(filename))
        return filename

    @property
    def _env_vars(self):
        """Required environment variables to make crane work on openshift"""
        return [('OPENSHIFT_PYTHON_WSGI_APPLICATION', 'crane/wsgi.py'),
                ('OPENSHIFT_PYTHON_DOCUMENT_ROOT', 'crane/')]

    def _call_openshift(self, url, req_type='get', payload=None):
        headers = {'authorization': 'Bearer ' + self._token}
        if not url.startswith(self._server_url):
            url = '{0}/{1}'.format(self._server_url, url)
        if req_type == 'get':
            logging.info('Calling Openshift URL "{0}"'.format(url))
            r = requests.get(url, headers=headers)
        elif req_type == 'post':
            logging.info('Posting to Openshift URL "{0}"'.format(url))
            r = requests.post(url, headers=headers, data=payload)
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

        if r.status_code >= 500:
            raise Exception('Received invalid status code: {0}'.format(r.status_code))

        return r_json

    def _set_env_vars(self):
        for key, val in self._env_vars:
            logging.info('Setting environment variable "{0}"'.format(key))
            payload = {'name': key, 'value': val}
            r_json = self._call_openshift(
                     self._app_data['links']['ADD_ENVIRONMENT_VARIABLE']['href'],
                     'post', payload)
            if r_json['status'] != 'created':
                raise Exception('Failed to set Openshift env variable')

    def _restart_app(self):
        logging.info('Restarting application..')
        payload = {'event': 'restart'}
        r_json = self._call_openshift(self.app_data['links']['RESTART']['href'],
                'post', payload)
        if r_json['status'] != 'ok':
            raise Exception('Failed to restart Openshift app')

    def clone_app(self):
        if not self._app_repo:
            logging.info('Clonning Openshift app "{0}"'.format(self.app_name))
            self._app_repo = Repo.clone_from(self.app_data['git_url'], self.app_local_dir)

    def verify_domain(self):
        """Verify that Openshift domain exists"""
        url = 'broker/rest/domains/{0}'.format(self.domain)
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
            if self._isv_app_name:
                cranefile = self._isv_app_crane_file
                print 'ISV app crane file "{0}" exists'.format(cranefile)
            else:
                print 'Skipping ISV app crane file check as ISV app name was not specified'
        except Exception as e:
            logging.error('Failed to verify Openshift status: {0}'.format(e))
            result = False
        return result

    def create_domain(self):
        try:
            self.verify_domain()
            logging.info('Openshift domain "{0}" already exists'.format(self.domain))
        except Exception:
            url = 'broker/rest/domains'
            payload = {'name': self.domain}
            logging.info('Creating Openshift domain "{0}"'.format(self.domain))
            r_json = self._call_openshift(url, 'post', payload)
            if r_json['status'] != 'created':
                raise Exception('Domain "{0}" could not be created'.format(self.domain))

    def create_app(self, redhat_meta=None):
        """Create an Openshift application"""
        try:
            self.verify_app()
            logging.info('Openshift app "{0}" already exists'.format(self.app_name))
        except Exception:
            payload = {'name'           : self.app_name,
                       'cartridge'      : self._cartridge,
                       'initial_git_url': self._app_git_url}
            url = 'broker/rest/domain/{0}/applications'.format(self.domain)
            logging.info('Creating OpenShift application..')
            r_json = self._call_openshift(url, 'post', payload)
            if r_json['status'] != 'created':
                raise Exception('Failed to create Openshift app')
            self._app_data = r_json['data']
            logging.info('Created Openshift app "{0}" with ID "{1}"'\
                         .format(self._app_data['app_url'], self._app_data['id']))
            self._set_env_vars()
            self._restart_app()
            sleep(5)
            if redhat_meta:
                self.update_app(redhat_meta)
            else:
                self.verify_app()

    def update_app(self, data_files):
        """Copy all config data_files to the crane/data directory"""
        if not data_files:
            logging.info('No configuration data supplied')
            return
        self.verify_app()
        self.clone_app()
        logging.info('Updating Openshift crane app configuration')
        dest_dir = os.path.join(self.app_local_dir, 'crane', 'data')
        files_to_add = []
        for i in data_files:
            shutil.copy(i, dest_dir)
            files_to_add.append(os.path.join(dest_dir, os.path.basename(i)))
        self._app_repo.index.add(files_to_add)
        self._app_repo.index.commit('Updated crane configuration')
        self._app_repo.remotes.origin.push()
        sleep(5)
        self.verify_app()

    def cleanup(self):
        if self._app_local_dir:
            logging.info('Removing local Openshift app dir "{0}"'.format(self._app_local_dir))
            shutil.rmtree(self._app_local_dir)
            self._app_local_dir = None


class Configuration(object):
    """Configuration and utilities"""

    _CONFIG_FILE_NAME = 'raas.cfg'
    _CONFIG_REPO_ENV_VAR = 'RAAS_CONF_REPO'

    def __init__(self, isv, config_branch, isv_app_name=None, file_upload=None,
                 oodomain=None, ooapp=None, s3bucket=None):
        """Setup Configuration object.

        Use current working dir as local config if it exists,
        otherwise clone repo based on RAAS_CONF_REPO env var.
        """
        self._pulp_repo = None
        self._config_branch = config_branch
        self.isv = isv
        self.isv_app_name = isv_app_name
        self.file_upload = file_upload
        self.oodomain = oodomain
        self.ooapp = ooapp
        self.s3bucket = s3bucket

        if os.path.isfile(self._CONFIG_FILE_NAME):
            self._conf_dir = os.getcwd()
            logging.info('Using configuration in current dir "{0}"'.format(self._conf_dir))
            try:
                self._config_repo = Repo(self._conf_dir)
                logging.info('Found git repository in "{0}"'.format(self._conf_dir))
            except Exception:
                logging.info('No repository found in "{0}"'.format(self._conf_dir))
                self._config_repo = None
        else:
            repo_url = os.getenv(self._CONFIG_REPO_ENV_VAR)
            if not repo_url:
                raise Exception('Current working directory does not contain "{0}" configuration file ' + \
                        'and environment variable "{1}" is not set.'.format(self._CONFIG_FILE_NAME, self._CONFIG_REPO_ENV_VAR))
            self._conf_dir = mkdtemp()
            logging.info('Clonning config repo from "{0}:{1}" to "{2}"'.format(repo_url, self._config_branch, self._conf_dir))
            self._config_repo = Repo.clone_from(repo_url, self._conf_dir, branch=self._config_branch)

        self._conf_file = os.path.join(self._conf_dir, self._CONFIG_FILE_NAME)
        if not os.path.isfile(self._conf_file):
            raise Exception('Configuration file "{0}" not found'.format(self._conf_file))
        self._parsed_config = ConfigParser()
        self._parsed_config.read(self._conf_file)

        logging.info('Using conf file "{0}"'.format(self._conf_file))

        self._setup_isv_config_dirs()
        self._setup_isv_config_file()

    @property
    def config_branch(self):
        return self._config_branch

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

    @isv_app_name.setter
    def isv_app_name(self, val):
        if val:
            self._isv_app_name = val.replace('/', '-')
        else:
            self._isv_app_name = None
        logging.debug('ISV app name set to "{0}"'.format(self.isv_app_name))

    @property
    def oodomain(self):
        if self._oodomain:
            return self._oodomain
        else:
            return self.isv

    @oodomain.setter
    def oodomain(self, val):
        if val:
            if not val.isalnum():
                raise ValueError('Openshift domain "{0}" must contain only alphanumeric characters'.format(val))
            self._oodomain = val.lower()
            logging.debug('Openshift domain set to "{0}"'.format(self.isv))
        else:
            self._oodomain = None

    @property
    def ooapp(self):
        if self._ooapp:
            return self._ooapp
        else:
            return 'registry'

    @ooapp.setter
    def ooapp(self, val):
        if val:
            if not val.isalnum():
                raise ValueError('Openshift app name "{0}" must contain only alphanumeric characters'.format(val))
            self._ooapp = val.lower()
            logging.debug('Openshift app name set to "{0}"'.format(self.isv))
        self._ooapp = None

    @property
    def s3bucket(self):
        if self._s3bucket:
            return self._s3bucket
        else:
            return self.isv + '.bucket'

    @s3bucket.setter
    def s3bucket(self, val):
        if val:
            self._s3bucket = val.lower()
            logging.debug('S3 bucket name set to "{0}"'.format(self.isv))
        else:
            self._s3bucket = None

    @property
    def _pulp_redirect_url(self):
        """Returns Pulp server redirect URL for S3 bucket"""
        if self._isv_app_name:
            return '/'.join([self._parsed_config.get('aws', 'aws_url'),
                             self._parsed_config.get(self.isv, 's3_bucket'),
                             self._isv_app_name])
        else:
            return None

    @property
    def pulp_conf(self):
        return {'server_url'  : self._parsed_config.get('pulpserver', 'host'),
                'username'    : self._parsed_config.get('pulpserver', 'username'),
                'password'    : self._parsed_config.get('pulpserver', 'password'),
                'verify_ssl'  : self._parsed_config.getboolean('pulpserver', 'verify_ssl'),
                'isv'         : self.isv,
                'isv_app_name': self.isv_app_name,
                'redirect_url': self._pulp_redirect_url}

    @property
    def openshift_conf(self):
        return {'server_url'  : self._parsed_config.get('openshift', 'server_url'),
                'token'       : self._parsed_config.get('openshift', 'token'),
                'domain'      : self._parsed_config.get(self.isv, 'openshift_domain'),
                'app_name'    : self._parsed_config.get(self.isv, 'openshift_app'),
                'app_git_url' : self._parsed_config.get('openshift', 'app_git_url'),
                'cartridge'   : self._parsed_config.get('openshift', 'cartridge'),
                'isv_app_name': self._isv_app_name}

    @property
    def aws_conf(self):
        return {'bucket_name': self._parsed_config.get(self.isv, 's3_bucket'),
                'app_name'   : self._isv_app_name,
                'aws_key'    : self._parsed_config.get('aws', 'aws_access_key'),
                'aws_secret' : self._parsed_config.get('aws', 'aws_secret_access_key')}

    @property
    def redhat_meta_conf(self):
        return {'git_repo_url': self._parsed_config.get('redhat', 'metadata_repo'),
                'relpath'     : self._parsed_config.get('redhat', 'metadata_relpath')}

    @property
    def logfile(self):
        return os.path.join(self._logdir, date.today().isoformat() + '.log')

    def commit_all_changes(self):
        if self._config_repo:
            logging.info('Committing changes in configuration')
            # TODO: add crane config file from meta dir
            files = [self._conf_file, self.logfile]
            self._config_repo.index.add(files)
            self._config_repo.index.commit('Updated configuration by raas script')
            self._config_repo.remotes.origin.push()

    def _setup_isv_config_dirs(self):
        self._logdir = os.path.join(self._conf_dir, self.isv, 'logs')
        self._metadir = os.path.join(self._conf_dir, self.isv, 'metadata')
        if not os.path.exists(self._logdir):
            logging.info('Creating log dir "{0}"'.format(self._logdir))
            os.makedirs(self._logdir)
        if not os.path.exists(self._metadir):
            logging.info('Creating metadata dir "{0}"'.format(self._metadir))
            os.makedirs(self._metadir)

    def _setup_isv_config_file(self):
        """Setup config file defaults if not provided"""
        if not self._parsed_config.has_section(self.isv):
            logging.info('Creating default ISV section in config file')
            self._parsed_config.add_section(self.isv)
            self._parsed_config.set(self.isv, 'openshift_domain', self.oodomain)
            self._parsed_config.set(self.isv, 'openshift_app', self.ooapp)
            self._parsed_config.set(self.isv, 's3_bucket', self.s3bucket)
            with open(self._conf_file, 'w') as configfile:
                self._parsed_config.write(configfile)


def main():
    """Entrypoint for script"""
    isv_args = ['isv']
    isv_kwargs = {'metavar': 'ISV_NAME',
                  'help': 'ISV name matching config file section'}
    isv_app_args = ['isv_app']
    isv_app_kwargs = {'metavar': 'ISV_APP_NAME',
                      'help': 'ISV Application name. Example: "some/app"'}
    parser = ArgumentParser()
    parser.add_argument('-l', '--log', metavar='LOG_LEVEL', default='WARNING',
            help='Desired log level. Can be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL. Default is WARNING.')
    parser.add_argument('-n', '--nocommit', action='store_true',
                        help='Do not commit configuration. Development only.')
    parser.add_argument('-c', '--configenv', metavar='BRANCH', default='stage',
                        choices=['dev', 'stage', 'master'],
                        help='Working configuration environment branch to use: "dev", "stage", "master" (production). Matches configuration repo branch. Default is "stage"')
    subparsers = parser.add_subparsers(help='sub-command help', dest='action')
    status_parser = subparsers.add_parser('status', help='Check configuration status')
    status_parser.add_argument(*isv_args, **isv_kwargs)
    status_parser.add_argument('-a', '--isv_app', **isv_app_kwargs)
    status_parser.add_argument('-p', '--pulp', action='store_true',
                        help='Include checking the pulp server status')
    setup_parser = subparsers.add_parser('setup', help='Setup initial configuration')
    setup_parser.add_argument(*isv_args, **isv_kwargs)
    setup_parser.add_argument('--oodomain', help='Openshift domain for this ISV, default is ISV name')
    setup_parser.add_argument('--ooapp', help='Openshift crane app name for this ISV, default is "registry"')
    setup_parser.add_argument('--s3bucket', help='AWS S3 bucket name for this ISV, default is [ISV_NAME].bucket')
    publish_parser = subparsers.add_parser('publish', help='Publish new or updated image')
    publish_parser.add_argument(*isv_args, **isv_kwargs)
    publish_parser.add_argument(*isv_app_args, **isv_app_kwargs)
    pulp_upload_parser = subparsers.add_parser('pulp-upload', help='Upload image to pulp')
    pulp_upload_parser.add_argument(*isv_args, **isv_kwargs)
    pulp_upload_parser.add_argument(*isv_app_args, **isv_app_kwargs)
    pulp_upload_parser.add_argument('file_upload', metavar='IMAGE.tar', help='File to upload to pulp server. Output of of "docker save some/image > image.tar".')
    args = parser.parse_args()

    logFormatter = logging.Formatter('%(asctime)s - {0} - %(name)s - %(levelname)s - %(message)s'.format(args.action.upper()))
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(logFormatter)
    log_level = getattr(logging, args.log.upper(), None)
    if not isinstance(log_level, int):
        print 'Invalid value passed to the --log option. See help for possible values.'
        sys.exit(1)
    consoleHandler.setLevel(log_level)
    logger.addHandler(consoleHandler)

    try:
        config_kwargs = {}
        if hasattr(args, 'isv_app'):
            if args.isv_app:
                p = re.compile('^.+/.+$')
                if p.match(args.isv_app):
                    config_kwargs['isv_app_name'] = args.isv_app
                else:
                    raise Exception('Application name "{0}" must contain "/", for example "some/app"'.format(args.isv_app))
        if hasattr(args, 'file_upload'):
            config_kwargs['file_upload'] = args.file_upload
        config_kwargs['config_branch'] = args.configenv
        if hasattr(args, 'oodomain'):
            config_kwargs['oodomain'] = args.oodomain
        if hasattr(args, 'ooapp'):
            config_kwargs['ooapp'] = args.ooapp
        if hasattr(args, 's3bucket'):
            config_kwargs['s3bucket'] = args.s3bucket
        config = Configuration(args.isv, **config_kwargs)
    except Exception as e:
        logging.critical('Failed to initialize raas: {0}'.format(e))
        sys.exit(1)

    fileHandler = logging.FileHandler(config.logfile)
    fileHandler.setFormatter(logFormatter)
    fileHandler.setLevel(logging.DEBUG)
    logger.addHandler(fileHandler)

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

    try:
        pulp = PulpServer(**config.pulp_conf)
    except Exception as e:
        logging.critical('Failed to initialize Pulp: {0}'.format(e))
        sys.exit(1)

    try:
        rhmeta = RedHatMeta(**config.redhat_meta_conf)
    except Exception as e:
        logging.critical('Failed to initialize Red Hat Meta class: {0}'.format(e))
        sys.exit(1)

    ret = 0

    if args.action in 'status':
        status = True
        if args.pulp:
            try:
                pulp.status()
                pulp.remove_orphan_content()
            except Exception as e:
                logging.error('Failed to verify Pulp status: {0}'.format(e))
                status = False
        if not aws.status():
            status = False
        if not openshift.status():
            status = False
        if status and openshift.isv_app_name:
            if openshift.image_ids == aws.image_ids:
                print 'Openshift Crane images matches AWS images'
            else:
                logging.error('Openshift Crane images does not match AWS images:\nCrane: {0}\nAWS: {1}'\
                              .format(openshift.image_ids, aws.image_ids))
                status = False
        if status:
            print 'Status of "{0}" should be OK'.format(config.isv)
        else:
            print 'Failed to verify status of "{0}"'.format(config.isv)
            ret = 1

    elif args.action in 'setup':
        try:
            aws.create_bucket()
            openshift.create_domain()
            openshift.create_app(rhmeta.redhat_meta_files)
            print 'ISV "{0}" was setup correctly'.format(config.isv)
        except Exception as e:
            logging.error('Failed to setup ISV: {0}'.format(e))
            ret = 1

    elif args.action in 'publish':
        try:
            pulp.status()
            pulp.verify_repo()
            pulp.update_redirect_url()
            pulp.export_repo()
            pulp.download_repo()
            aws.upload_layers(pulp.files_for_aws(rhmeta.image_ids))
            openshift.update_app([pulp.crane_config_file])
        except Exception as e:
            logging.error('Failed to publish image from Pulp: {0}'.format(e))
            ret = 1

    elif args.action in 'pulp-upload':
        try:
            pulp.status()
        except Exception as e:
            logging.error('Failed to initialize Pulp: {0}'.format(e))
            sys.exit(1)
        if not pulp.is_repo():
            try:
                pulp.create_repo()
            except Exception as e:
                logging.error('Failed to create Pulp repository: {0}'.format(e))
                sys.exit(1)
        else:
            logging.info('Pulp repository "{0}" already exists'.format(pulp.repo_id))
        try:
            pulp.upload_image(config.file_upload)
            print 'Uploaded image to pulp repo "{0}"'.format(pulp.repo_id)
        except Exception as e:
            logging.error('Failed to upload image to Pulp: {0}'.format(e))
            sys.exit(1)

    if not args.nocommit:
        config.commit_all_changes()

    openshift.cleanup()
    pulp.cleanup()
    rhmeta.cleanup()

    sys.exit(ret)


if __name__ == '__main__':
    main()
