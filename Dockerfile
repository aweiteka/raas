FROM centos:7.0.1406
MAINTAINER Aaron Weitekamp <aweiteka@redhat.com>

RUN yum install -y http://dl.fedoraproject.org/pub/epel/7/x86_64/e/epel-release-7-5.noarch.rpm
RUN yum install -y git python-pip ruby rubygems

ADD requirements.txt /raas/
ADD raas.py /raas/

RUN pip install -r /raas/requirements.txt
RUN pip install awscli
RUN gem install rhc

WORKDIR /raas
CMD ["bash"]

