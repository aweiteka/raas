#!/usr/bin/env python
# -*- coding: utf-8 -*-

# raas - docker registry tooling that integrates with Pulp and Crane
# Copyright (C) 2015  Red Hat, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import json
import logging
import os
import re
import requests
import shutil
import sys
import tarfile

from argparse import ArgumentParser, RawDescriptionHelpFormatter
from boto import connect_s3, s3
from ConfigParser import SafeConfigParser
from datetime import date
from git import Repo
from git.exc import InvalidGitRepositoryError
from glob import glob
from simplejson.scanner import JSONDecodeError
from tempfile import mkdtemp
from time import sleep


class PulpError(Exception):
    pass


class PulpServer(object):
    """Interact with pulp API"""

    _WEB_DISTRIBUTOR    = 'docker_web_distributor_name_cli'
    _EXPORT_DISTRIBUTOR = 'docker_export_distributor_name_cli'
    _IMPORTER           = 'docker_importer'
    _EXPORT_DIR         = '/var/www/pub/docker/web/'
    _UNIT_TYPE_ID       = 'docker_image'
    _CHUNK_SIZE         = 1048576 # 1 MB per upload call

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
        if not self._repo_id:
            if not self._isv_app_name:
                logging.error('ISV app name is required for pulp repo ID')
                raise ConfigurationError('Missing ISV app name')
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
            logging.info('Getting pulp upload ID')
            url = '{0}/pulp/api/v2/content/uploads/'.format(self.server_url)
            r_json = self._call_pulp(url, 'post')
            self._upload_id = r_json['upload_id']
            logging.info('Received pulp upload ID: {0}'.format(self._upload_id))
        return self._upload_id

    def _call_pulp(self, url, req_type='get', payload=None, return_json=True, p_stream=False):
        if req_type == 'get':
            logging.info('Calling pulp URL "{0}"'.format(url))
            r = requests.get(url, auth=(self._username, self._password), verify=self._verify_ssl, stream=p_stream)
        elif req_type == 'post':
            logging.info('Posting to pulp URL "{0}"'.format(url))
            if payload:
                logging.debug('Pulp HTTP payload:\n{0}'.format(json.dumps(payload, indent=2)))
            r = requests.post(url, auth=(self._username, self._password),
                    data=json.dumps(payload), headers={'content-type': 'application/json'}, verify=self._verify_ssl)
        elif req_type == 'put':
            # some calls pass in binary data so we don't log payload data or json encode it here
            logging.info('Putting to pulp URL "{0}"'.format(url))
            r = requests.put(url, auth=(self._username, self._password), data=payload, verify=self._verify_ssl)
        elif req_type == 'delete':
            logging.info('Delete call to pulp URL "{0}"'.format(url))
            r = requests.delete(url, auth=(self._username, self._password), verify=self._verify_ssl)
        else:
            logging.error('Invalid value of "req_type" parameter: {0}'.format(req_type))
            raise ValueError('Invalid value of "req_type" parameter')

        logging.debug('Pulp HTTP status code: {0}'.format(r.status_code))
        if r.status_code >= 500:
            logging.error('Received invalid status code from pulp: {0}'.format(r.status_code))
            raise PulpError('Received invalid status code: {0}'.format(r.status_code))

        if return_json:
            try:
                r_json = r.json()
            except JSONDecodeError as e:
                logging.error('Failed to parse pulp response: {0}'.format(e))
                raise PulpError('Failed to parse pulp response: {0}'.format(e))
            # some requests return null
            if not r_json:
                return r_json
            logging.debug('Pulp JSON response:\n{0}'.format(json.dumps(r_json, indent=2)))

            if 'error_message' in r_json:
                logging.warn('Error messages from Pulp response: {0}'.format(r_json['error_message']))
                raise PulpError('Received error messages from pulp: {0}'.format(r_json['error_message']))

            if 'spawned_tasks' in r_json:
                for task in r_json['spawned_tasks']:
                    self._watch_task(task['task_id'], task['_href'])
            return r_json
        else:
            return r

    def _watch_task(self, tid, thref, timeout=60, poll=5):
        """Watch a task ID and return when it finishes or fails"""
        logging.info('Waiting up to "{0}" seconds for pulp task "{1}"'.format(timeout, tid))
        curr = 0
        while curr < timeout:
            t = self._call_pulp('{0}{1}'.format(self.server_url, thref))
            if t['state'] == 'finished':
                logging.info('Pulp subtask "{0}" completed'.format(tid))
                return
            elif t['state'] == 'error':
                logging.error('Pulp subtask "{0}" had an error: {1}'.format(tid, t['error']))
                logging.debug('Traceback from pulp subtask "{0}":\n{1}'.format(tid, t['traceback']))
                raise PulpError('Pulp task "{0}" failed'.format(tid))
            else:
                logging.debug('Waiting for pulp task "{0}" ({1}/{2} seconds passed)'.format(tid, curr, timeout))
                print 'Waiting for pulp task... ({1}/{2} seconds passed)'.format(curr, timeout)
                sleep(poll)
                curr += poll
        logging.error('Timed out waiting for pulp task "{0}"'.format(tid))
        raise PulpError('Timed out waiting for pulp task "{0}"'.format(tid))

    def status(self):
        """Check pulp server status"""
        logging.info('Checking pulp status')
        self._call_pulp('{0}/pulp/api/v2/status/'.format(self.server_url))
        logging.info('Pulp status looks OK')
        print 'Pulp status is OK'

    def verify_repo(self):
        """Verify pulp repository exists"""
        url = '{0}/pulp/api/v2/repositories/{1}/'.format(self.server_url, self.repo_id)
        logging.info('Verifying pulp repository "{0}"'.format(self.repo_id))
        self._call_pulp(url)
        logging.info('Pulp repository "{0}" looks OK'.format(self.repo_id))
        print 'Pulp repository is OK'

    def _create_repo(self):
        """Create pulp docker repository"""
        try:
            self.verify_repo()
            logging.info('Pulp repository "{0}" already exists'.format(self.repo_id))
            print 'Pulp repository "{0}" already exists'.format(self.repo_id)
        except PulpError:
            payload = {
                'id': self.repo_id,
                'display_name': '{0} {1}'.format(self._isv, self._isv_app_name),
                'description': 'docker image repository for ISV {0}'.format(self._isv),
                'notes': {
                    '_repo-type': 'docker-repo'
                },
                'importer_type_id': self._IMPORTER,
                'importer_config': {},
                'distributors': [{
                    'distributor_type_id': 'docker_distributor_web',
                    'distributor_id': self._WEB_DISTRIBUTOR,
                    'distributor_config': {
                        'repo-registry-id': self._isv_app_name},
                    'auto_publish': 'true'},
                    {
                    'distributor_type_id': 'docker_distributor_export',
                    'distributor_id': self._EXPORT_DISTRIBUTOR,
                    'distributor_config': {
                        'repo-registry-id': self._isv_app_name},
                    'auto_publish': 'false'}
                    ]
            }
            url = '{0}/pulp/api/v2/repositories/'.format(self.server_url)
            logging.info('Creating pulp repository "{0}"'.format(self.repo_id))
            self._call_pulp(url, 'post', payload)
            logging.info('Created pulp repository "{0}"'.format(self.repo_id))
            print 'Created pulp repository "{0}"'.format(self.repo_id)

    def _update_redirect_url(self):
        """Update distributor redirect URL and export file"""
        url = '{0}/pulp/api/v2/repositories/{1}/'.format(self.server_url, self.repo_id)
        payload = {
            'distributor_configs': {
                self._EXPORT_DISTRIBUTOR: {
                    'redirect-url': self._redirect_url
                }
            }
        }
        logging.info('Updating pulp repository "{0}" URL to "{1}"'.format(self.repo_id, self._redirect_url))
        self._call_pulp(url, 'put', json.dumps(payload))
        logging.info('Updated pulp repository "{0}" URL to "{1}"'.format(self.repo_id, self._redirect_url))

    def _delete_upload_id(self):
        """Delete upload request ID"""
        if self._upload_id:
            logging.info('Deleting pulp upload ID "{0}"'.format(self._upload_id))
            url = '{0}/pulp/api/v2/content/uploads/{1}/'.format(self.server_url, self._upload_id)
            self._call_pulp(url, 'delete')
            self._upload_id = None
            logging.info('Deleted pulp upload ID "{0}"'.format(self._upload_id))
        else:
            logging.info('Pulp upload ID is not set')

    def _extract_image(self, file_upload):
        if os.path.isfile(os.path.join(self.data_dir, 'repositories')):
            logging.info('Image is already extracted in "{0}"'.format(self.data_dir))
            return
        logging.info('Extracting image "{0}" to "{1}"'.format(file_upload, self.data_dir))
        print 'Extracting image "{0}" to "{1}"'.format(file_upload, self.data_dir)
        with tarfile.open(file_upload) as tar:
            tar.extractall(self.data_dir)
        logging.info('Image "{0}" extracted to "{1}"'.format(file_upload, self.data_dir))

    def _get_app_name_from_image(self, file_upload):
        logging.info('Getting app name from the image "{0}"'.format(file_upload))
        print 'Getting app name from the image "{0}"'.format(file_upload)
        self._extract_image(file_upload)
        with open(os.path.join(self.data_dir, 'repositories')) as f:
            data = json.load(f)
        self._isv_app_name = data.keys()[0]
        if not self._isv_app_name:
            logging.error('Missing app name in the "repositories" file')
            raise PulpError('Missing app name in the "repositories" file')
        logging.info('Got "{0}" as app name from the image "{1}"'.format(
                self._isv_app_name, file_upload))
        print 'Got "{0}" as app name from the image "{1}"'.format(
                self._isv_app_name, file_upload)
        self._isv_app_name = self._isv_app_name.replace('/', '-')

    def _get_hierarchy_from_image(self, file_upload):
        logging.info('Getting layers hierarchy from image "{0}"'.format(file_upload))
        hierarchy = []
        self._extract_image(file_upload)
        glob_path = os.path.join(self.data_dir, '*', 'json')
        logging.info('Checking image metadata files: {0}'.format(glob_path))
        files = glob(glob_path)
        logging.debug('Image metadata files: {0}'.format(files))
        if not files:
            logging.error('Missing json metadata files in docker image "{0}"'.format(file_upload))
            raise PulpError('Missing json metadata files in docker image')
        for fl in files:
            logging.debug('Inspecting file "{0}"'.format(fl))
            with open(fl) as f:
                data = json.load(f)
            logging.debug('Content of file "{0}":\n{1}'.format(fl,
                    json.dumps(data, indent=2)))
            image_id = data['id']
            logging.debug('Image ID is "{0}"'.format(image_id))
            if not 'parent' in data:
                if not image_id in hierarchy:
                    hierarchy.append(image_id)
                    logging.debug('Parent ID is missing, adding image ID at the end')
                else:
                    logging.debug('Parent ID is missing and image ID is already in the hierarchy')
                continue
            parent = data['parent']
            logging.debug('Parent image ID is "{0}"'.format(parent))
            if not image_id in hierarchy and not parent in hierarchy:
                logging.debug('Adding both parent ID and image ID at the beginning of hierarchy')
                hierarchy.insert(0, parent)
                hierarchy.insert(0, image_id)
            elif image_id not in hierarchy:
                logging.debug('Parent ID is in the hierarchy, adding image ID before parent')
                hierarchy.insert(hierarchy.index(parent), image_id)
            elif parent not in hierarchy:
                logging.debug('Image ID is in the hierarchy, adding parent after image ID')
                index = hierarchy.index(image_id)
                if index == len(hierarchy) - 1:
                    hierarchy.append(parent)
                else:
                    hierarchy.insert(index + 1, parent)
            logging.debug('Current state of hierarchy: {0}'.format(hierarchy))
        logging.info('Got layers hierarchy from image "{0}"'.format(file_upload))
        logging.debug('Final layers hierarchy in image: {0}'.format(hierarchy))
        return hierarchy

    def upload_image(self, file_upload, redhat_image_ids):
        """Upload image to pulp repository"""
        if not os.path.isfile(file_upload):
            logging.error('Cannot find file to upload to pulp "{0}"'.format(file_upload))
            raise PulpError('Cannot find file "{0}"'.format(file_upload))
        self.status()
        if not self._isv_app_name:
            self._get_app_name_from_image(file_upload)
        mask_id = None
        for i in self._get_hierarchy_from_image(file_upload):
            if i in redhat_image_ids:
                mask_id = i
                logging.info('Masking Red Hat image ID "{0}" in pulp upload'.format(mask_id))
                break
        else:
            logging.info('Not masking any Red Hat image ID in pulp upload')
        self._create_repo()
        self._upload_bits(file_upload)
        self._import_upload(mask_id)
        self._delete_upload_id()
        self._publish_repo()
        logging.info('Image "{0}" uploaded to pulp repo "{1}"'.format(
                file_upload, self.repo_id))
        print 'Image "{0}" uploaded to pulp repo "{1}"'.format(
                file_upload, self.repo_id)

    def _upload_bits(self, file_upload):
        logging.info('Uploading file "{0}" to pulp'.format(file_upload))
        offset = 0
        source_file_size = os.path.getsize(file_upload)
        with open(file_upload, 'r') as f:
            while True:
                f.seek(offset)
                data = f.read(self._CHUNK_SIZE)
                if not data:
                    break
                url = '{0}/pulp/api/v2/content/uploads/{1}/{2}/'.format(self.server_url, self.upload_id, offset)
                logging.info('Uploading "{0}": {1:.1f} of {2:.1f} MB done'.format(file_upload,  offset / 1048576.0, source_file_size / 1048576.0))
                print 'Uploading file "{0}" to pulp: {1:.1f} of {2:.1f} MB done'.format(file_upload, offset / 1048576.0, source_file_size / 1048576.0)
                self._call_pulp(url, 'put', data)
                offset = min(offset + self._CHUNK_SIZE, source_file_size)
        logging.info('File "{0}" uploaded to pulp'.format(file_upload))
        print 'File "{0}" uploaded to pulp'.format(file_upload)

    def _import_upload(self, mask_id=None):
        """Import uploaded content"""
        logging.info('Importing pulp upload {0} into {1}'.format(self.upload_id, self.repo_id))
        url = '{0}/pulp/api/v2/repositories/{1}/actions/import_upload/'.format(self.server_url, self.repo_id)
        payload = {
            'unit_type_id': self._UNIT_TYPE_ID,
            'upload_id': self.upload_id,
            'unit_key': {},
            'unit_metadata': {},
            'override_config': {},
        }
        if mask_id:
            payload['override_config']['mask_id'] = mask_id
        self._call_pulp(url, 'post', payload)
        logging.info('Imported pulp upload {0} into {1}'.format(self.upload_id, self.repo_id))

    def _publish_repo(self):
        """Publish pulp repository to pulp web server"""
        url = '{0}/pulp/api/v2/repositories/{1}/actions/publish/'.format(self.server_url, self.repo_id)
        payload = {
            'id': self._WEB_DISTRIBUTOR,
            'override_config': {}
        }
        logging.info('Publishing pulp repository "{0}"'.format(self.repo_id))
        self._call_pulp(url, 'post', payload)
        logging.info('Published pulp repository "{0}"'.format(self.repo_id))

    def _export_repo(self):
        """Export pulp repository to pulp web server as tar file.

        The tarball is split into the layer components and crane metadata.
        It is for the purpose of uploading to remote crane server"""
        url = '{0}/pulp/api/v2/repositories/{1}/actions/publish/'.format(self.server_url, self.repo_id)
        payload = {
            'id': self._EXPORT_DISTRIBUTOR,
            'override_config': {
                'export_file': '{0}{1}.tar'.format(self._EXPORT_DIR, self.repo_id)
            }
        }
        logging.info('Exporting pulp repository "{0}"'.format(self.repo_id))
        self._call_pulp(url, 'post', payload)
        logging.info('Exported pulp repository "{0}"'.format(self.repo_id))

    def remove_orphan_content(self, content_type='docker_image'):
        """Remove orphan content"""
        if self._list_orphans(content_type):
            logging.info('Removing orphaned "{0}" content'.format(content_type))
            url = '{0}/pulp/api/v2/content/orphans/{1}/'.format(self.server_url, content_type)
            self._call_pulp(url, 'delete')
            logging.info('Removed orphaned "{0}" content'.format(content_type))

    def _list_orphans(self, content_type='docker_image'):
        """List (log) orphan content. Defaults to docker content"""
        url = '{0}/pulp/api/v2/content/orphans/{1}/'.format(self.server_url, content_type)
        logging.info('Getting orphan "{0}" content'.format(content_type))
        r_json = self._call_pulp(url)
        content = [content['image_id'] for content in r_json]
        logging.info('Orphan "{0}" content: {1}'.format(content_type, content))
        return content

    def download_repo(self):
        self.status()
        self.verify_repo()
        self._update_redirect_url()
        self._export_repo()

        url = '{0}/pulp/docker/{1}.tar'.format(self.server_url, self.repo_id)
        logging.info('Downloading exported repo "{0}"'.format(self.repo_id))
        print 'Downloading exported repo "{0}"'.format(self.repo_id)
        r = self._call_pulp(url, 'get', return_json=False, p_stream=True)
        with open(self.exported_local_file, 'wb') as fd:
            for chunk in r.iter_content(self._CHUNK_SIZE):
                fd.write(chunk)
        logging.info('Exported repo downloaded to "{0}"'.format(self.exported_local_file))
        logging.info('Extracting downloaded repo "{0}"'.format(self.exported_local_file))
        print 'Extracting downloaded repo "{0}"'.format(self.exported_local_file)
        with tarfile.open(self.exported_local_file) as tar:
            tar.extractall(self.data_dir)
        logging.info('Downloaded repo extracted to "{0}"'.format(self.data_dir))
        print 'Downloaded repo extracted to "{0}"'.format(self.data_dir)

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
            logging.error('No files to upload to AWS')
            raise PulpError('No files to upload to AWS')
        return files

    def cleanup(self):
        if self._data_dir:
            logging.info('Removing pulp data dir "{0}"'.format(self._data_dir))
            shutil.rmtree(self._data_dir)
            self._data_dir = None


class AwsError(Exception):
    pass


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
            self.verify_bucket()
            logging.info('Getting S3 bucket "{0}"'.format(self.bucket_name))
            self._bucket = self._conn.get_bucket(self.bucket_name)
        return self._bucket

    @property
    def image_ids(self):
        if not self._image_ids:
            if not self._app_name:
                logging.error('ISV app name is required for S3 image IDs')
                raise ConfigurationError('Missing ISV app name')
            logging.info('Getting S3 image IDs for "{0}"'.format(self._app_name))
            for i in self.bucket.list(prefix=self._app_name + '/', delimiter='/'):
                self._image_ids.add(i.name.split('/')[1])
            logging.debug('S3 image IDs: {0}'.format(self._image_ids))
        return self._image_ids

    def _connect(self, aws_key, aws_secret):
        logging.info('Connecting to AWS')
        self._conn = connect_s3(aws_access_key_id=aws_key,
                aws_secret_access_key=aws_secret)

    def verify_bucket(self):
        logging.info('Looking up S3 bucket "{0}"'.format(self.bucket_name))
        if not self._conn.lookup(self.bucket_name):
            logging.warn('S3 bucket "{0}" does not exist'.format(self.bucket_name))
            raise AwsError('Failed to find S3 bucket "{0}"'.format(self.bucket_name))
        logging.info('S3 bucket "{0}" looks OK'.format(self.bucket_name))
        print 'S3 bucket "{0}" looks OK'.format(self.bucket_name)

    def status(self):
        logging.info('Checking AWS status')
        self.bucket
        logging.info('AWS looks OK')
        print 'AWS status is OK'

    def create_bucket(self):
        try:
            self.verify_bucket()
            logging.info('S3 bucket "{0}" already exists'.format(self.bucket_name))
            print 'S3 bucket "{0}" already exists'.format(self.bucket_name)
        except AwsError:
            logging.info('Creating S3 bucket "{0}"'.format(self.bucket_name))
            self._bucket = self._conn.create_bucket(self.bucket_name)
            logging.info('Created S3 bucket "{0}"'.format(self.bucket_name))
            print 'Created S3 bucket "{0}"'.format(self.bucket_name)

    def upload_layers(self, files):
        """Upload image layers to S3 bucket"""
        logging.info('Uploading files to S3 bucket "{0}"'.format(self.bucket_name))
        if not self._app_name:
            logging.error('ISV app name is required for S3 image upload')
            raise ConfigurationError('Missing ISV app name')
        for name, path in files:
            dest = '/'.join([self._app_name, name])
            key = s3.key.Key(bucket=self.bucket, name=dest)
            logging.debug('Uploading "{0}"'.format(dest))
            print 'Uploading "{0}" file to "{1}" S3 bucket'.format(dest, self.bucket_name)
            with open(path, 'rb') as f:
                key.set_contents_from_file(f, replace=True)
            key.set_acl('public-read')
            logging.debug('Uploaded "{0}"'.format(dest))
        logging.info('All files uploaded to S3 bucket "{0}"'.format(self.bucket_name))


class OpenshiftError(Exception):
    pass


class Openshift(object):
    """Interact with Openshift REST API"""

    def __init__(self, server_url, token, domain, app_name, app_scale,
            app_git_url, app_git_branch, cartridge, isv, isv_app_name):
        self._app_data = None
        self._app_local_dir = None
        self._app_repo = None
        self._isv_app_crane_file = None
        self._image_ids = set()
        self._server_url = server_url
        self._token = token
        self._domain = domain
        self._app_name = app_name
        self._app_scale = app_scale
        self._app_git_url = app_git_url
        self._app_git_branch = app_git_branch
        self._cartridge = cartridge
        self._isv = isv
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
            logging.info('Created local openshift app dir "{0}"'.format(self._app_local_dir))
        return self._app_local_dir

    @property
    def app_data(self):
        if not self._app_data:
            url = 'broker/rest/domain/{0}/applications'.format(self.domain)
            logging.info('Getting openshift app data for "{0}"'.format(self.app_name))
            r_json = self._call_openshift(url)
            if r_json['status'] != 'ok':
                logging.error('Failed to get applications in domain "{0}"'.format(self.domain))
                raise OpenshiftError('Failed to get applications in domain "{0}"'.format(self.domain))
            for app in r_json['data']:
                logging.debug('Inspecting openshift app "{0}" with ID "{1}"'.format(app['name'], app['id']))
                if app['name'] == self.app_name:
                    logging.info('Found openshift app "{0}" with ID "{1}"'.format(app['name'], app['id']))
                    self._app_data = app
                    break
            else:
                logging.warn('Application "{0}" not found in domain "{1}"'.format(self.app_name, self.domain))
                raise OpenshiftError('Openshift application "{0}" not found'.format(self.app_name))
        return self._app_data

    @property
    def image_ids(self):
        if not self._image_ids:
            with open(self.isv_app_crane_file) as f:
                data = json.load(f)
            logging.debug('Crane "{0}.json" data:\n{1}'.format(self.isv_app_name, json.dumps(data, indent=2)))
            self._image_ids = [i['id'] for i in data['images']]
            self._image_ids = set(self._image_ids)
            logging.debug('Crane image IDs: {0}'.format(self._image_ids))
        return self._image_ids

    @property
    def isv_app_crane_file(self):
        if not self._isv_app_crane_file:
            if not self.isv_app_name:
                logging.error('ISV app name is required to get proper crane config file')
                raise ConfigurationError('Missing ISV app name')
            self.clone_app()
            filename = os.path.join(self.app_local_dir, 'crane', 'data', '-'.join([self._isv, self.isv_app_name + '.json']))
            if not os.path.isfile(filename):
                logging.warn('ISV app crane file "{0}" does not exist'.format(filename))
                raise OpenshiftError('Missing ISV app crane file')
            logging.info('Using ISV app crane file "{0}"'.format(filename))
            print 'Using ISV app crane file "{0}"'.format(filename)
            self._isv_app_crane_file = filename
        return self._isv_app_crane_file

    def _call_openshift(self, url, req_type='get', payload=None):
        headers = {'authorization': 'Bearer ' + self._token}
        if not url.startswith(self._server_url):
            url = '{0}/{1}'.format(self._server_url, url)
        if req_type == 'get':
            logging.info('Calling openshift URL "{0}"'.format(url))
            r = requests.get(url, headers=headers)
        elif req_type == 'post':
            logging.info('Posting to openshift URL "{0}"'.format(url))
            logging.debug('Posting data: {0}'.format(json.dumps(payload, indent=2)))
            headers['content-type'] = 'application/json'
            r = requests.post(url, headers=headers, data=json.dumps(payload))
        elif req_type == 'put':
            logging.info('Putting to openshift URL "{0}"'.format(url))
            logging.debug('Putting data: {0}'.format(json.dumps(payload, indent=2)))
            headers['content-type'] = 'application/json'
            r = requests.put(url, headers=headers, data=json.dumps(payload))
        else:
            logging.error('Invalid value of "req_type" parameter: {0}'.format(req_type))
            raise ValueError('Invalid value of "req_type" parameter')

        logging.debug('Openshift HTTP status code: {0}'.format(r.status_code))

        try:
            r_json = r.json()
        except JSONDecodeError as e:
            logging.error('Failed to parse openshift response: {0}'.format(e))
            raise OpenshiftError('Failed to parse openshift response: {0}'.format(e))
        logging.debug('Openshift JSON response:\n{0}'.format(json.dumps(r_json, indent=2)))

        if r_json['messages']:
            msgs = ''
            for m in r_json['messages']:
                msgs += '\n - ' + m['text']
            logging.info('Messages from Openshift response:{0}'.format(msgs))

        if r.status_code >= 500:
            logging.error('Received invalid status code from openshift: {0}'.format(r.status_code))
            raise OpenshiftError('Received invalid status code: {0}'.format(r.status_code))

        return r_json

    def clone_app(self):
        if not self._app_repo:
            logging.info('Clonning openshift application "{0}" to "{1}"'.format(self.app_name, self.app_local_dir))
            print 'Clonning openshift application "{0}" to "{1}"'.format(self.app_name, self.app_local_dir)
            self._app_repo = Repo.clone_from(self.app_data['git_url'],
                    self.app_local_dir, branch=self._app_git_branch)

    def verify_domain(self):
        """Verify that Openshift domain exists"""
        url = 'broker/rest/domains/{0}'.format(self.domain)
        logging.info('Verifying openshift domain "{0}"'.format(self.domain))
        r_json = self._call_openshift(url)
        if r_json['status'] != 'ok':
            logging.warn('Openshift domain "{0}" does not exist'.format(self.domain))
            raise OpenshiftError('Failed to find openshift domain "{0}"'.format(self.domain))
        logging.info('Openshift domain "{0}" looks OK'.format(self.domain))
        print 'Openshift domain "{0}" looks OK'.format(self.domain)

    def verify_app(self):
        url = self.app_data['app_url'] + 'v1/_ping'
        logging.info('Verifying openshift crane app status on url "{0}"'.format(url))
        r = requests.get(url)
        logging.debug('Openshift crane app HTTP status code: {0}'.format(r.status_code))
        if r.status_code != 200:
            logging.warn('Openshift crane app ping HTTP status code is not "200" but: {0}'.format(r.status_code))
            raise OpenshiftError('Failed to ping openshift crane app')
        logging.debug('Openshift crane app response: {0}'.format(r.text))
        if r.text != 'true':
            logging.warn('Openshift crane ping response is not "true"')
            logging.debug('Openshift crane ping response is not "true" but: {0}'.format(r.text))
            raise OpenshiftError('Failed to ping openshift crane app')
        logging.info('Openshift crane app on "{0}" looks OK'.format(self.app_data['app_url']))
        print 'Openshift crane app on "{0}" looks OK'.format(self.app_data['app_url'])

    def status(self):
        logging.info('Checking openshift status')
        self.verify_domain()
        self.verify_app()
        if self.isv_app_name:
            self.isv_app_crane_file
        else:
            logging.info('Skipping ISV app crane file check as ISV app name was not specified')
            print 'Skipping ISV app crane file check as ISV app name was not specified'
        logging.info('Openshift status looks OK')
        print 'Openshift status is OK'

    def create_domain(self):
        try:
            self.verify_domain()
            logging.info('Openshift domain "{0}" already exists'.format(self.domain))
            print 'Openshift domain "{0}" already exists'.format(self.domain)
        except OpenshiftError:
            url = 'broker/rest/domains'
            payload = {'name': self.domain}
            logging.info('Creating openshift domain "{0}"'.format(self.domain))
            r_json = self._call_openshift(url, 'post', payload)
            if r_json['status'] != 'created':
                logging.error('Domain "{0}" could not be created'.format(self.domain))
                raise OpenshiftError('Domain "{0}" could not be created'.format(self.domain))
            logging.info('Created openshift domain "{0}"'.format(self.domain))
            print 'Created openshift domain "{0}"'.format(self.domain)

    def create_app(self, redhat_meta=None):
        """Create an openshift application"""
        try:
            self.verify_app()
            logging.info('Openshift app "{0}" already exists'.format(self.app_name))
            print 'Openshift app "{0}" already exists'.format(self.app_name)
        except OpenshiftError:
            payload = {
                'name'                 : self.app_name,
                'cartridge'            : self._cartridge,
                'initial_git_url'      : self._app_git_url,
                'scale'                : self._app_scale,
                'environment_variables': [{
                    'name' : 'OPENSHIFT_PYTHON_WSGI_APPLICATION',
                    'value': 'crane/wsgi.py',
                }, {
                    'name' : 'OPENSHIFT_PYTHON_DOCUMENT_ROOT',
                    'value': 'crane/',
                }, {
                    'name' : 'HAPROXY_CARTRIDGE_HTTPCHK_URI',
                    'value': '/v1/_ping',}]
            }
            url = 'broker/rest/domain/{0}/applications'.format(self.domain)
            logging.info('Creating openshift application "{0}"'.format(self.app_name))
            if self._app_scale:
                scalable = 'scalable '
            else:
                scalable = ''
            print 'Creating {0}openshift application "{1}" (this can take several minutes..)'.format(
                    scalable, self.app_name)
            r_json = self._call_openshift(url, 'post', payload)
            if r_json['status'] != 'created':
                logging.error('Failed to create openshift app "{0}"'.format(self.app_name))
                raise OpenshiftError('Failed to create openshift app "{0}"'.format(self.app_name))
            self._app_data = r_json['data']

            if self._app_git_branch != 'master':
                payload = {'deployment_branch': self._app_git_branch}
                logging.info('Updating openshift application "{0}"'.format(self.app_name))
                r_json = self._call_openshift(self.app_data['links']['UPDATE']['href'], 'put', payload)
                if r_json['status'] != 'ok':
                    logging.error('Failed to update openshift app "{0}"'.format(self.app_name))
                    raise OpenshiftError('Failed to update openshift app "{0}"'.format(self.app_name))
                self._app_data = r_json['data']

            if redhat_meta:
                self.update_app(redhat_meta)
            elif self._app_git_branch != 'master':
                logging.info('Deploying openshift application "{0}"'.format(self.app_name))
                r_json = self._call_openshift(self.app_data['links']['DEPLOY']['href'], 'post', {})
                if r_json['status'] != 'created':
                    logging.error('Failed to deploy openshift app "{0}"'.format(self.app_name))
                    raise OpenshiftError('Failed to deploy openshift app "{0}"'.format(self.app_name))
                self.verify_app()
            else:
                self.verify_app()

            logging.info('Created openshift app "{0}" with ID "{1}"'\
                         .format(self.app_data['app_url'], self.app_data['id']))
            print 'Created openshift application "{0}"'.format(self.app_name)

    def update_app(self, data_files):
        """Copy all config data_files to the crane/data directory"""
        logging.info('Updating openshift crane app "{0}"'.format(self.app_name))
        if not data_files:
            logging.info('No configuration data supplied')
            return
        print 'Updating openshift crane application "{0}"'.format(self.app_name)
        self.clone_app()
        dest_dir = os.path.join(self.app_local_dir, 'crane', 'data')
        files_to_add = []
        for i in data_files:
            logging.debug('Copying file "{0}" to openshit crane data dir'.format(i))
            shutil.copy(i, dest_dir)
            files_to_add.append(os.path.join(dest_dir, os.path.basename(i)))
        self._app_repo.index.add(files_to_add)
        self._app_repo.index.commit('Updated crane configuration')
        self._app_repo.remotes.origin.push()
        self.verify_app()
        logging.info('Openshift crane app "{0}" has been updated'.format(self.app_name))
        print 'Updated openshift crane application "{0}"'.format(self.app_name)

    def cleanup(self):
        if self._app_local_dir:
            logging.info('Removing local openshift app dir "{0}"'.format(self._app_local_dir))
            shutil.rmtree(self._app_local_dir)
            self._app_local_dir = None


class ConfigurationError(Exception):
    pass


class Configuration(object):
    """Configuration and utilities"""

    _CONFIG_FILE_NAME    = 'raas.cfg'
    _CONFIG_REPO_ENV_VAR = 'RAAS_CONF_REPO'

    def __init__(self, isv, config_branch, isv_app_name=None, file_upload=None,
            oodomain=None, ooapp=None, ooscale=True, s3bucket=None):
        """Setup Configuration object.

        Use current working dir as local config if it exists,
        otherwise clone repo based on RAAS_CONF_REPO env var.
        """
        self._pulp_repo = None
        self._redhat_image_ids = set()
        self.config_branch = config_branch
        self.isv = isv
        self.isv_app_name = isv_app_name
        self.file_upload = file_upload
        self.oodomain = oodomain
        self.ooapp = ooapp
        self.ooscale = ooscale
        self.s3bucket = s3bucket

        if os.path.isfile(self._CONFIG_FILE_NAME):
            self._conf_dir = os.getcwd()
            logging.info('Using configuration in current dir "{0}"'.format(self._conf_dir))
            try:
                self._config_repo = Repo(self._conf_dir)
                logging.info('Found git repository in current dir "{0}"'.format(self._conf_dir))
            except InvalidGitRepositoryError:
                self._config_repo = None
                logging.info('No repository found in current dir "{0}"'.format(self._conf_dir))
        else:
            repo_url = os.getenv(self._CONFIG_REPO_ENV_VAR)
            if not repo_url:
                logging.error('Current working directory does not contain "{0}" ' + \
                        'configuration file and environment variable "{1}" is ' + \
                        'not set. One of these two options is required.'\
                        .format(self._CONFIG_FILE_NAME, self._CONFIG_REPO_ENV_VAR))
                raise ConfigurationError('Configuration file in current dir or ' + \
                        '"{0}" env var is required'.format(self._CONFIG_REPO_ENV_VAR))
            self._conf_dir = mkdtemp()
            logging.info('Clonning config repo from "{0}:{1}" to "{2}"'.format(
                    repo_url, self._config_branch, self._conf_dir))
            self._config_repo = Repo.clone_from(repo_url, self._conf_dir,
                    branch=self._config_branch)

        self._conf_file = os.path.join(self._conf_dir, self._CONFIG_FILE_NAME)
        if not os.path.isfile(self._conf_file):
            logging.error('Config file "{0}" not found'.format(self._conf_file))
            raise ConfigurationError('Missing config file')
        self._parsed_config = SafeConfigParser()
        self._parsed_config.read(self._conf_file)
        logging.info('Loaded config file "{0}"'.format(self._conf_file))

        self._setup_isv_config_dirs()
        self._setup_isv_config_file()

    @property
    def config_branch(self):
        return self._config_branch

    @config_branch.setter
    def config_branch(self, val):
        if not val:
            logging.error('Git config branch is not defined')
            raise ValueError('Git config branch is not defined')
        self._config_branch = val.lower()
        logging.debug('Git config branch set to "{0}"'.format(self._config_branch))

    @property
    def isv(self):
        return self._isv

    @isv.setter
    def isv(self, val):
        if not val.isalnum():
            logging.error('ISV "{0}" must contain only alphanumeric characters'.format(val))
            raise ValueError('Invalid ISV name "{0}"'.format(val))
        if len(val) > 16:
            logging.error('ISV "{0}" must not be longer than 16 characters'.format(val))
            raise ValueError('Invalid ISV name "{0}"'.format(val))
        self._isv = val.lower()
        logging.debug('ISV set to "{0}"'.format(self._isv))

    @property
    def isv_app_name(self):
        return self._isv_app_name

    @isv_app_name.setter
    def isv_app_name(self, val):
        if val:
            if val.count('/') > 1:
                logging.error('ISV app name must contain no more than one "/": {0}'.format(val))
                raise ValueError('Invalid ISV app name "{0}"'.format(val))
            val = val.lower()
            if val.count('/') == 1:
                repo, app = val.split('/')
            else:
                repo = None
                app = val
            if repo:
                if not 4 <= len(repo) <= 30:
                    logging.error('Namespace part of ISV app name must have between 4 and 30 characters: {0}'.format(repo))
                    raise ValueError('Invalid ISV app name "{0}"'.format(val))
                if not re.match('^[a-z0-9_]+$', repo):
                    logging.error('Namespace part of ISV app name must contain only [a-z0-9_] characters: {0}'.format(repo))
                    raise ValueError('Invalid ISV app name "{0}"'.format(val))
            if not re.match('^[a-z0-9-_.]+$', app):
                logging.error('App name part of ISV app name must contain only [a-z0-9-_.] characters: {0}'.format(app))
                raise ValueError('Invalid ISV app name "{0}"'.format(val))
            self._isv_app_name = val.replace('/', '-')
        else:
            self._isv_app_name = None
        logging.debug('ISV app name set to "{0}"'.format(self._isv_app_name))

    @property
    def oodomain(self):
        return self._oodomain

    @oodomain.setter
    def oodomain(self, val):
        if val:
            if not val.isalnum():
                logging.error('Openshift domain "{0}" must contain only alphanumeric characters'.format(val))
                raise ValueError('Invalid openshift domain "{0}"'.format(val))
            if len(val) > 16:
                logging.error('Openshift domain "{0}" must not be longer than 16 characters'.format(val))
                raise ValueError('Invalid openshift domain "{0}"'.format(val))
            self._oodomain = val.lower()
        else:
            self._oodomain = self.isv
        logging.debug('Openshift domain set to "{0}"'.format(self._oodomain))

    @property
    def ooapp(self):
        return self._ooapp

    @ooapp.setter
    def ooapp(self, val):
        if val:
            if not val.isalnum():
                logging.error('Openshift app name "{0}" must contain only alphanumeric characters'.format(val))
                raise ValueError('Invalid openshift app name "{0}"'.format(val))
            if len(val) > 32:
                logging.error('Openshift app name "{0}" must not be longer than 32 characters'.format(val))
                raise ValueError('Invalid openshift app name "{0}"'.format(val))
            self._ooapp = val.lower()
        else:
            self._ooapp = 'registry'
        logging.debug('Openshift app name set to "{0}"'.format(self._ooapp))

    @property
    def ooscale(self):
        return self._ooscale

    @ooscale.setter
    def ooscale(self, val):
        if not isinstance(val, bool):
            logging.error('Openshift scale param "{0}" must be boolean'.format(val))
            raise ValueError('Invalid openshift scale param "{0}"'.format(val))
        self._ooscale = val
        logging.debug('Openshift scale param set to "{0}"'.format(self._ooscale))

    @property
    def s3bucket(self):
        return self._s3bucket

    @s3bucket.setter
    def s3bucket(self, val):
        if val:
            val = val.lower()
            if not re.match('^[a-z0-9-_.]+$', val):
                logging.error('S3 bucket name "{0}" must contain only [a-z0-9-_.] characters'.format(val))
                raise ValueError('Invalid S3 bucket name "{0}"'.format(val))
            if len(val) > 32:
                logging.error('S3 bucket name "{0}" must not be longer than 32 characters'.format(val))
                raise ValueError('Invalid S3 bucket name "{0}"'.format(val))
            self._s3bucket = val
        else:
            self._s3bucket = self.isv + '.bucket'
        logging.debug('S3 bucket name set to "{0}"'.format(self._s3bucket))

    @property
    def _pulp_redirect_url(self):
        """Returns pulp redirect URL for S3 bucket"""
        if not self._isv_app_name:
            logging.info('Pulp redirect URL is None')
            return None
        r_url = '/'.join([self._parsed_config.get('aws', 'aws_url'),
                self._parsed_config.get(self.isv, 's3_bucket'),
                self._isv_app_name])
        logging.info('Pulp redirect URL: {0}'.format(r_url))
        return r_url

    @property
    def logfile(self):
        l_file = os.path.join(self._logdir, date.today().isoformat() + '.log')
        logging.debug('Using "{0}" as log file'.format(l_file))
        return l_file

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
        return {'server_url'    : self._parsed_config.get('openshift', 'server_url'),
                'token'         : self._parsed_config.get('openshift', 'token'),
                'domain'        : self._parsed_config.get(self.isv, 'openshift_domain'),
                'app_name'      : self._parsed_config.get(self.isv, 'openshift_app'),
                'app_scale'     : self._parsed_config.getboolean(self.isv, 'openshift_scale'),
                'app_git_url'   : self._parsed_config.get('openshift', 'app_git_url'),
                'app_git_branch': self._parsed_config.get('openshift', 'app_git_branch'),
                'cartridge'     : self._parsed_config.get('openshift', 'cartridge'),
                'isv'           : self.isv,
                'isv_app_name'  : self._isv_app_name}

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
    def redhat_meta_files(self):
        glob_path = os.path.join(self._conf_dir, 'redhat', 'metadata') + os.sep + '*.json'
        logging.info('Looking for Red Hat meta files in "{0}"'.format(glob_path))
        rhmeta_files = glob(glob_path)
        if not rhmeta_files:
            logging.error('No Red Hat meta files found')
            raise ConfigurationError('No Red Hat meta files found')
        logging.debug('Found Red Hat meta files: {0}'.format(rhmeta_files))
        return rhmeta_files

    @property
    def redhat_image_ids(self):
        if not self._redhat_image_ids:
            for filename in self.redhat_meta_files:
                logging.debug('Reading Red Hat meta file "{0}"'.format(filename))
                with open(filename) as f:
                    data = json.load(f)
                logging.debug('Red Hat meta file "{0}" data:\n{1}'.format(
                        filename, json.dumps(data, indent=2)))
                for i in data['images']:
                    self._redhat_image_ids.add(i['id'])
            logging.debug('Red Hat image IDs: {0}'.format(self._redhat_image_ids))
        return self._redhat_image_ids

    def commit_all_changes(self):
        if self._config_repo:
            logging.info('Committing changes in config repo')
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
        logging.debug('Using "{0}" as log dir'.format(self._logdir))
        logging.debug('Using "{0}" as meta dir'.format(self._metadir))

    def _setup_isv_config_file(self):
        """Setup config file defaults if not provided"""
        if not self._parsed_config.has_section(self.isv):
            logging.info('Creating default ISV section in config file')
            self._parsed_config.add_section(self.isv)
            self._parsed_config.set(self.isv, 'openshift_domain', self.oodomain)
            self._parsed_config.set(self.isv, 'openshift_app', self.ooapp)
            self._parsed_config.set(self.isv, 'openshift_scale', str(self.ooscale))
            self._parsed_config.set(self.isv, 's3_bucket', self.s3bucket)
            with open(self._conf_file, 'w') as configfile:
                self._parsed_config.write(configfile)
            logging.debug('ISV openshift domain set to "{0}"'.format(self.oodomain))
            logging.debug('ISV openshift app name set to "{0}"'.format(self.ooapp))
            logging.debug('ISV openshift scale set to "{0}"'.format(self.ooscale))
            logging.debug('ISV S3 bucket name set to "{0}"'.format(self.s3bucket))


class RaasError(Exception):
    pass


def main():
    """Entrypoint for script"""
    isv_args = ['isv']
    isv_kwargs = {'metavar': 'ISV_NAME',
            'help': 'ISV name matching config file section'}
    isv_app_args = ['isv_app']
    isv_app_opt_args = ['-a', '--isv_app']
    isv_app_kwargs = {'metavar': 'ISV_APP_NAME',
            'help': 'ISV application name, for example: "some/app"'}
    parser = ArgumentParser(
            formatter_class=RawDescriptionHelpFormatter,
            description='This script is used to automate publishing of certified docker images from\nISVs (Independent Software Vendors)',
            epilog='raas  Copyright (C) 2015  Red Hat, Inc.\nThis program comes with ABSOLUTELY NO WARRANTY.\nThis is free software, ' + \
                    'and you are welcome to redistribute it\nunder certain conditions; see LICENSE file for details.')
    parser.add_argument('-n', '--nocommit', action='store_true',
            help='do not commit configuration (development only)')
    parser.add_argument('-l', '--log', metavar='LOG_LEVEL', default='ERROR',
            choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
            help='desired log level one of "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL". Default is "ERROR"')
    parser.add_argument('-c', '--configenv', metavar='BRANCH', default='stage',
            choices=['dev', 'stage', 'master'],
            help='working configuration environment branch to use: "dev", "stage", "master" (production). Matches configuration repo branch. Default is "stage"')
    subparsers = parser.add_subparsers(dest='action')
    status_parser = subparsers.add_parser('status',
            help='check configuration status')
    status_parser.add_argument(*isv_args, **isv_kwargs)
    status_parser.add_argument(*isv_app_opt_args, **isv_app_kwargs)
    status_parser.add_argument('-p', '--pulp', action='store_true',
            help='include checking the pulp server status')
    setup_parser = subparsers.add_parser('setup',
            help='setup initial configuration')
    setup_parser.add_argument(*isv_args, **isv_kwargs)
    setup_parser.add_argument('--oodomain',
            help='openshift domain for this ISV if ISV is not set in config file, default is ISV name')
    setup_parser.add_argument('--ooapp',
            help='openshift crane app name for this ISV if ISV is not set in config file, default is "registry"')
    setup_parser.add_argument('--oonoscale', action='store_false',
            help='disable scaling of openshift crane app if not set in config file; by default, scaling is enabled')
    setup_parser.add_argument('--s3bucket',
            help='AWS S3 bucket name for this ISV if ISV is not set in config file, default is [ISV_NAME].bucket')
    publish_parser = subparsers.add_parser('publish',
            help='publish new or updated image')
    publish_parser.add_argument(*isv_args, **isv_kwargs)
    publish_parser.add_argument(*isv_app_args, **isv_app_kwargs)
    pulp_upload_parser = subparsers.add_parser('pulp-upload',
            help='upload image to pulp')
    pulp_upload_parser.add_argument(*isv_args, **isv_kwargs)
    pulp_upload_parser.add_argument(*isv_app_opt_args, **isv_app_kwargs)
    pulp_upload_parser.add_argument('file_upload', metavar='IMAGE.tar',
            help='file to upload to pulp server. Output of "docker save some/image > image.tar"')
    args = parser.parse_args()

    logFormatter = logging.Formatter('%(asctime)s - {0} - %(name)s - %(levelname)s - %(message)s'.format(args.action.upper()))
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(logFormatter)
    consoleHandler.setLevel(getattr(logging, args.log.upper(), None))
    logger.addHandler(consoleHandler)

    try:
        config_kwargs = {}
        if hasattr(args, 'isv_app'):
            config_kwargs['isv_app_name'] = args.isv_app
        if hasattr(args, 'file_upload'):
            config_kwargs['file_upload'] = args.file_upload
        if hasattr(args, 'oodomain'):
            config_kwargs['oodomain'] = args.oodomain
        if hasattr(args, 'ooapp'):
            config_kwargs['ooapp'] = args.ooapp
        if hasattr(args, 'oonoscale'):
            config_kwargs['ooscale'] = args.oonoscale
        if hasattr(args, 's3bucket'):
            config_kwargs['s3bucket'] = args.s3bucket
        config_kwargs['config_branch'] = args.configenv
        config = Configuration(args.isv, **config_kwargs)
    except ConfigurationError as e:
        logging.critical('Failed to initialize raas: {0}'.format(e))
        sys.exit(1)
    except ValueError as e:
        logging.critical('Invalid value provided: {0}'.format(e))
        sys.exit(1)
    except IOError as e:
        logging.critical('I/O error: {0}'.format(e))
        sys.exit(1)

    fileHandler = logging.FileHandler(config.logfile)
    fileHandler.setFormatter(logFormatter)
    fileHandler.setLevel(logging.DEBUG)
    logger.addHandler(fileHandler)

    try:
        openshift = Openshift(**config.openshift_conf)
    except OpenshiftError as e:
        logging.critical('Failed to initialize Openshift: {0}'.format(e))
        sys.exit(1)

    try:
        aws = AwsS3(**config.aws_conf)
    except AwsError as e:
        logging.critical('Failed to initialize AWS: {0}'.format(e))
        sys.exit(1)

    try:
        pulp = PulpServer(**config.pulp_conf)
    except PulpError as e:
        logging.critical('Failed to initialize Pulp: {0}'.format(e))
        sys.exit(1)

    ret = 0

    if args.action == 'status':
        try:
            if args.pulp:
                pulp.status()
                pulp.remove_orphan_content()
                if config.isv_app_name:
                    pulp.verify_repo()
            aws.status()
            openshift.status()
            if config.isv_app_name:
                if openshift.image_ids == aws.image_ids:
                    logging.info('Openshift crane images matches AWS images')
                    print 'Openshift crane images matches AWS images'
                else:
                    logging.error('Openshift Crane images does not match AWS images:\nCrane: {0}\nAWS: {1}'\
                            .format(openshift.image_ids, aws.image_ids))
                    raise RaasError('Openshift crane images and AWS images do not match')
            logging.info('Status of "{0}" is OK'.format(config.isv))
            print 'Status of "{0}" is OK'.format(config.isv)
        except RaasError as e:
            logging.error('Failed to verify "{0}" status: {1}'.format(config.isv, e))
            ret = 1
        except AwsError as e:
            logging.error('Failed to verify AWS status: {0}'.format(e))
            ret = 1
        except OpenshiftError as e:
            logging.error('Failed to verify openshift status: {0}'.format(e))
            ret = 1
        except PulpError as e:
            logging.error('Failed to verify pulp status: {0}'.format(e))
            ret = 1
        except IOError as e:
            logging.error('I/O error: {0}'.format(e))
            ret = 1

    elif args.action == 'setup':
        try:
            aws.create_bucket()
            openshift.create_domain()
            openshift.create_app(config.redhat_meta_files)
            logging.info('ISV "{0}" was setup correctly'.format(config.isv))
            print 'ISV "{0}" was setup correctly'.format(config.isv)
        except AwsError as e:
            logging.error('Failed to create S3 bucket: {0}'.format(e))
            ret = 1
        except OpenshiftError as e:
            logging.error('Failed to setup openshift: {0}'.format(e))
            ret = 1
        except IOError as e:
            logging.error('I/O error: {0}'.format(e))
            ret = 1

    elif args.action == 'publish':
        try:
            pulp.download_repo()
            aws.upload_layers(pulp.files_for_aws(config.redhat_image_ids))
            openshift.update_app([pulp.crane_config_file])
            logging.info('Published "{0}" image'.format(config.isv_app_name))
            print 'Published "{0}" image'.format(config.isv_app_name)
        except PulpError as e:
            logging.error('Failed to download repo from pulp: {0}'.format(e))
            ret = 1
        except AwsError as e:
            logging.error('Failed to upload images to AWS: {0}'.format(e))
            ret = 1
        except OpenshiftError as e:
            logging.error('Failed to update openshift app: {0}'.format(e))
            ret = 1
        except IOError as e:
            logging.error('I/O error: {0}'.format(e))
            ret = 1

    elif args.action == 'pulp-upload':
        try:
            pulp.upload_image(config.file_upload, config.redhat_image_ids)
        except PulpError as e:
            logging.error('Failed to upload image to pulp: {0}'.format(e))
            ret = 1
        except IOError as e:
            logging.error('I/O error: {0}'.format(e))
            ret = 1

    if not args.nocommit:
        config.commit_all_changes()

    openshift.cleanup()
    pulp.cleanup()

    sys.exit(ret)


if __name__ == '__main__':
    main()
