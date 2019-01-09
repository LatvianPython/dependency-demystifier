import time
import svn.exception
import configparser
import keyring
import logging
import re
from logging.config import fileConfig
from contextlib import suppress
from getpass import getpass
from slackclient import SlackClient
from DependencyChecker import DependencyChecker
from DependencyChecker import format_as_slack_attachment

fileConfig('logger.ini')
logger = logging.getLogger(__name__)


def main():
    config = configparser.ConfigParser()
    config.read('DependencyChecker.conf')

    statuses_to_ignore = config['JIRA']['statuses_to_ignore'].split(',')
    service_name = config['JIRA']['keyring_service_name']
    server = config['JIRA']['server']
    username = config['JIRA']['username']
    password = keyring.get_password(service_name=service_name, username=username)

    if password is None:
        keyring.set_password(service_name=service_name, username=username,
                             password=getpass('Input jira password for {}: '.format(username)))
        password = keyring.get_password(service_name=service_name, username=username)

    issue_regex = re.compile(config['SVN']['issuekey_regex'])

    file_extensions = config['SVN']['accepted_extensions'].split(',')
    working_copy_path = config['SVN']['working_copy_path']
    dev_branch = config['SVN']['dev_branch']

    dependency_checker = DependencyChecker(jira=(server, username, password),
                                           svn_working_copy_path=working_copy_path, extensions_to_check=file_extensions,
                                           statuses_to_ignore=statuses_to_ignore, issue_regex=issue_regex,
                                           dev_branch=dev_branch)

    slack = SlackClient(config['SLACK']['token'])

    if slack.rtm_connect():
        while slack.server.connected is True:
            events = slack.rtm_read()
            for event in events:
                if event['type'] == 'message':
                    with suppress(KeyError):
                        if 'bot_id' not in event:
                            logger.info('got request from:"{}" for "{}"'.format(event['user'], event['text']))
                            logger.debug(event)
                            try:
                                # fixme: don't like the look of this piece of code, could say the same for whole file
                                issues = issue_regex.findall(event['text'])
                                if len(issues) >= 5:
                                    logger.warning('USER:"{}" tried to get summary for {} issued'.format(event['user'],
                                                                                                         len(issues)))
                                    slack.api_call("chat.postMessage",
                                                   channel=event['channel'],
                                                   text='Too many issues in one call! Do less than 5 :x:')
                                    continue
                                elif len(issues) > 0:
                                    dependency_summary = []
                                    for issue in issues:
                                        dependencies = dependency_checker.get_dependencies_as_dict(issue_key=issue)
                                        dependency_summary += format_as_slack_attachment(dependencies=dependencies,
                                                                                         jira_server=server)
                                else:
                                    revision = int(event['text'])
                                    dependencies = dependency_checker.get_dependencies_as_dict(
                                        revision_to_check=revision)
                                    dependency_summary = format_as_slack_attachment(dependencies=dependencies,
                                                                                    jira_server=server)
                                logger.debug(dependency_summary)
                                slack.api_call("chat.postMessage",
                                               channel=event['channel'],
                                               attachments=dependency_summary)
                                logger.info('success!')
                            except ValueError:
                                logger.warning('USER:"{}" tried to enter bad revision number'.format(event['user']))
                                slack.api_call("chat.postMessage",
                                               channel=event['channel'],
                                               text='Enter just a plain revision number! :x:')
                            except svn.exception.SvnException as e:
                                if 'No such revision' in e.args:
                                    logger.warning('svn.exception.SvnException: No such revision ({})'.format(event))
                                    slack.api_call("chat.postMessage",
                                                   channel=event['channel'],
                                                   text='No such revision! :x:')
                                else:
                                    logger.error('USER:"{}" Unknown SvnException'.format(event['user']), exc_info=1)
                                    raise

            # slack does not allow more than 1 message post per sec
            time.sleep(1)
    else:
        logger.critical('Connection Failed')


if __name__ == '__main__':
    try:
        main()
    except (SystemExit, KeyboardInterrupt):
        pass
    except:
        logger.critical('', exc_info=1)
        raise
