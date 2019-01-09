from svn import local
import re
from pathlib import Path
from jira import JIRA
from collections import namedtuple
import time
import logging

Issue = namedtuple(typename='issue', field_names=['issue_key', 'status'])

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

class DependencyChecker:
    def __init__(self, jira, svn_working_copy_path, extensions_to_check, statuses_to_ignore, issue_regex,
                 max_revisions=20):
        server, username, password = jira
        self.jira = JIRA(server=server, auth=(username, password))
        self.file_extensions = extensions_to_check
        self.statuses_to_ignore = statuses_to_ignore
        self.max_checked_revisions = max_revisions
        self.issue_regex = re.compile(issue_regex)
        self.svn = local.LocalClient(svn_working_copy_path)

    def get_issue_keys(self, log_message):
        return set(self.issue_regex.findall(log_message))

    def get_dependencies(self, revision_to_check):
        logger.debug(revision_to_check)
        log_entry = next(self.svn.log_default(revision_from=revision_to_check, revision_to=revision_to_check,
                                              limit=1, changelist=True))

        logger.debug('log_entry = '.format(log_entry))

        files = [file for _, file in log_entry.changelist if Path(file).suffix in self.file_extensions]

        logger.debug('files_found: ({}); {}'.format(len(files), files))

        try:  # fixme: provide actual solution to this
            main_issue_key = self.get_issue_keys(log_message=log_entry.msg).pop()
        except IndexError:
            main_issue_key = None

        logger.debug('main_issue_key = {}'.format(main_issue_key))

        dependencies = []
        for file in files:
            revisions = self.svn.log_default(rel_filepath=file)

            open_issues = set()
            for i, revision in enumerate(revisions):

                if revision.revision > revision_to_check:
                    continue
                if i > self.max_checked_revisions:
                    break

                issues_in_revision = self.get_issue_keys(log_message=revision.msg)
                logger.debug('{} revision({}) = {}'.format(file, revision.revision, issues_in_revision))
                if main_issue_key not in issues_in_revision:
                    for issue in issues_in_revision:
                        if issue in open_issues:
                            continue

                        issue_status = self.jira.issue(id=issue, fields='status').fields.status.name
                        logger.debug('{} {}'.format(issue, issue_status))

                        if issue_status not in self.statuses_to_ignore:
                            open_issue = Issue(issue, issue_status)
                            open_issues.add(open_issue)
            dependencies.append((Path(file).name, open_issues))
        return main_issue_key, dependencies

    def get_dependencies_as_dict(self, revision_to_check):
        main_issue_key, dependencies = self.get_dependencies(revision_to_check=revision_to_check)
        return {'main_issue_key': main_issue_key,
                'revision': revision_to_check,
                'files': {file_name: open_issues
                          for file_name, open_issues in dependencies}}


def format_as_slack_attachment(dependencies, jira_server):
    summary = dependencies.copy()
    summary['files'] = {file_name: {status: [issue.issue_key for issue in issues if issue.status == status]
                                    for status in set(issue.status for issue in issues)}
                        for file_name, issues in summary['files'].items()}

    fields = [{'title': '{} {}'.format(file, ':heavy_check_mark:' if len(issues) == 0 else ':warning:'),
               'value': '\n'.join('{}:\n•{}'.format(status, '\n•'.join(issues))
                                  for status, issues in issues.items()),
               'short': False}
              for file, issues in summary['files'].items()]

    had_any_dependencies = any(len(issues) > 0 for _, issues in summary['files'].items())

    logger.debug('had_dependencies: {}'.format(had_any_dependencies))

    attachment = {
        'fallback': 'fallback',  # todo: provide actual fallback
        'color': 'warning' if had_any_dependencies else 'good',
        'title': summary['main_issue_key'],
        'title_link': '{}/browse/{}'.format(jira_server, summary['main_issue_key']),
        'text': 'Summary for revision: {}'.format(summary['revision']),
        'fields': fields,
        'ts': time.time()
    }
    return [attachment]
