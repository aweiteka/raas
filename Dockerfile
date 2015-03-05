FROM centos:7.0.1406
MAINTAINER Aaron Weitekamp <aweiteka@redhat.com>

RUN yum install -y http://dl.fedoraproject.org/pub/epel/7/x86_64/e/epel-release-7-5.noarch.rpm
RUN yum install -y git python-pip ruby rubygems

ADD express.conf /root/.openshift/express.conf
ADD requirements.txt /root/raas/
ADD raas.py /root/raas/
ADD VERSION /root/

RUN pip install -r /root/raas/requirements.txt
RUN pip install awscli
RUN gem install rhc

RUN ln -s /root/raas/raas.py /usr/bin/raas

CMD ["bash"]

