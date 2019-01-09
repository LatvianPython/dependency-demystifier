import time
import svn.exception
import configparser
import keyring
import logging
from contextlib import suppress
from getpass import getpass
from slackclient import SlackClient
from DependencyChecker import DependencyChecker
from DependencyChecker import format_as_slack_attachment


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(asctime)-15s %(funcName)s: %(message)s')

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

    issue_regex = config['SVN']['issuekey_regex']
    file_extensions = config['SVN']['accepted_extensions'].split(',')
    working_copy_path = config['SVN']['working_copy_path']

    dependency_checker = DependencyChecker(jira=(server, username, password),
                                           svn_working_copy_path=working_copy_path, extensions_to_check=file_extensions,
                                           statuses_to_ignore=statuses_to_ignore, issue_regex=issue_regex)

    slack = SlackClient(config['SLACK']['token'])

    if slack.rtm_connect():
        while slack.server.connected is True:
            events = slack.rtm_read()
            for event in events:
                if event['type'] == 'message':
                    with suppress(KeyError):
                        if 'bot_id' not in event:
                            with suppress(Exception):
                                logging.info('{} {}'.format(event['user'], event['text']))
                            try:
                                dependencies = dependency_checker.get_dependencies_as_dict(int(event['text']))
                                dependency_summary = format_as_slack_attachment(dependencies, server)
                                slack.api_call("chat.postMessage",
                                               channel=event['channel'],
                                               attachments=dependency_summary)
                            except ValueError:
                                slack.api_call("chat.postMessage",
                                               channel=event['channel'],
                                               text='Enter just a plain revision number! :x:')
                            except svn.exception.SvnException:
                                slack.api_call("chat.postMessage",
                                               channel=event['channel'],
                                               text='No such revision! :x:')

            # slack does not allow more than 1 message post per sec
            time.sleep(1)
    else:
        print("Connection Failed")


if __name__ == '__main__':
    main()
