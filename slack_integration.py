import time
import svn.exception
import configparser
import keyring
import logging
import re
from logging.config import fileConfig
from jira.exceptions import JIRAError
from getpass import getpass
from slackclient import SlackClient
from DependencyChecker import DependencyChecker
from DependencyChecker import format_as_slack_attachment

fileConfig('logger.ini')
logger = logging.getLogger(__name__)


class SlackApp:

    def __init__(self):
        config = configparser.ConfigParser()
        config.read('DependencyChecker.conf')

        statuses_to_ignore = config['JIRA']['statuses_to_ignore'].split(',')
        service_name = config['JIRA']['keyring_service_name']
        self.jira_server = config['JIRA']['server']
        username = config['JIRA']['username']
        password = keyring.get_password(service_name=service_name, username=username)

        if password is None:
            keyring.set_password(service_name=service_name, username=username,
                                 password=getpass('Input jira password for {}: '.format(username)))
            password = keyring.get_password(service_name=service_name, username=username)

        self.issue_regex = re.compile(config['SVN']['issuekey_regex'])

        file_extensions = config['SVN']['accepted_extensions'].split(',')
        working_copy_path = config['SVN']['working_copy_path']
        dev_branch = config['SVN']['dev_branch']

        self.slack = SlackClient(config['SLACK']['token'])
        self.dependency_checker = DependencyChecker(jira=(self.jira_server, username, password),
                                                    svn_working_copy_path=working_copy_path,
                                                    extensions_to_check=file_extensions,
                                                    statuses_to_ignore=statuses_to_ignore, issue_regex=self.issue_regex,
                                                    dev_branch=dev_branch)

    def run_app(self):
        """Main function of app, used to handle events returned by the Slack Real Time Messaging API
        """
        if self.slack.rtm_connect():
            while self.slack.server.connected is True:
                events = self.slack.rtm_read()
                for event in events:
                    if event['type'] == 'message':
                        self.handle_message_event(event=event)
                # slack does not allow more than 1 message post per sec
                time.sleep(1)
        else:
            logger.critical('Connection Failed')

    def handle_message_event(self, event):
        if 'bot_id' in event:
            return

        logger.info('got request from:"{}" for "{}"'.format(event['user'], event['text']))
        logger.debug(event)
        try:
            issues = self.issue_regex.findall(event['text'].upper())
            if len(issues) >= 5:
                logger.warning('USER:"{}" tried to get summary for {} issued'.format(event['user'],
                                                                                     len(issues)))
                self.slack.api_call('chat.postMessage',
                                    channel=event['channel'],
                                    text='Too many issues in one call! Do less than 5 :x:')
                return
            elif len(issues) > 0:
                dependency_summary = []
                for issue in issues:
                    dependencies = self.dependency_checker.get_dependencies(issue_key=issue)
                    dependency_summary += format_as_slack_attachment(dependencies=dependencies,
                                                                     jira_server=self.jira_server)
            else:
                try:
                    revision = int(event['text'])
                except ValueError:
                    logger.warning('USER:"{}" tried to enter bad revision number'.format(event['user']))
                    self.slack.api_call('chat.postMessage',
                                        channel=event['channel'],
                                        text='Enter just a plain revision number! :x:')
                    return
                dependencies = self.dependency_checker.get_dependencies(revision_to_check=revision)

                if dependencies['issue_key'] is None:
                    logger.warning('USER:"{}" no issue key found for revision {}'.format(event['user'], revision))
                    self.slack.api_call('chat.postMessage',
                                        channel=event['channel'],
                                        text='No issue key found for revision! :x:')
                    return

                dependency_summary = format_as_slack_attachment(dependencies=dependencies,
                                                                jira_server=self.jira_server)
            logger.debug(dependency_summary)
            self.slack.api_call('chat.postMessage',
                                channel=event['channel'],
                                attachments=dependency_summary)
            logger.info('success!')
        except JIRAError as e:
            logger.warning('USER:"{}" caused jira instance to return an error: {}'.format(event['user'], e.args))
            if 'Issue Does Not Exist' == e.args[1]:
                self.slack.api_call('chat.postMessage',
                                    channel=event['channel'],
                                    text='Issue Does Not Exist! :x:')
            else:
                logger.error('USER:"{}" Unknown JIRAError'.format(event['user']))
                raise
        except svn.exception.SvnException as e:
            if any('No such revision' in arg for arg in e.args):
                logger.warning('svn.exception.SvnException: No such revision ({})'.format(event))
                self.slack.api_call('chat.postMessage',
                                    channel=event['channel'],
                                    text='No such revision! :x:')
            elif any('was not found' in arg for arg in e.args):
                logger.warning('svn.exception.SvnException: File not found ({})'.format(event))
                self.slack.api_call('chat.postMessage',
                                    channel=event['channel'],
                                    text='Revision was found, but file has been moved since. '
                                         'Could be due to checking an old revision! :x:')
            else:
                logger.error('USER:"{}" Unknown SvnException'.format(event['user']))
                raise


def main():
    app = SlackApp()
    app.run_app()


if __name__ == '__main__':
    try:
        main()
    except (SystemExit, KeyboardInterrupt):
        logger.info('Goodbye :)')
        pass
    except:
        logger.critical('', exc_info=1)
        raise
