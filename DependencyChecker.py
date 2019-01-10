from svn import local
import re
from pathlib import Path
from jira import JIRA
from collections import namedtuple
from datetime import datetime
import logging

Issue = namedtuple(typename='issue', field_names=['issue_key', 'status'])

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DependencyChecker:
    def __init__(self, jira, svn_working_copy_path, extensions_to_check, statuses_to_ignore, issue_regex,
                 dev_branch=None, max_revisions=20):
        server, username, password = jira
        self.jira = JIRA(server=server, auth=(username, password))
        self.file_extensions = extensions_to_check
        self.statuses_to_ignore = statuses_to_ignore
        self.max_checked_revisions = max_revisions
        self.issue_regex = issue_regex
        self.dev_branch = dev_branch
        self.svn = local.LocalClient(svn_working_copy_path)

    def get_issue_keys(self, log_message):
        return set(self.issue_regex.findall(log_message))

    def get_modified_files_for_revision(self, revision):
        log_entry = next(self.svn.log_default(revision_from=revision, revision_to=revision,
                                              limit=1, changelist=True))

        logger.debug('log_entry = '.format(log_entry))

        files = [file for _, file in log_entry.changelist if Path(file).suffix in self.file_extensions]

        try:  # fixme: provide actual solution to this
            issue_key = self.get_issue_keys(log_message=log_entry.msg).pop()
        except IndexError:
            issue_key = None
        return issue_key, files

    def get_modified_files_for_issue(self, issue_key):
        issue = self.jira.issue(id=issue_key, fields='created')
        search_start_date = datetime.strptime(issue.fields.created, '%Y-%m-%dT%H:%M:%S.000%z')

        # use cases currently do not require searching in specific branch, "hard-coding" to use dev branch when
        # going for dependencies just by issue key
        revisions = self.svn.log_default(timestamp_from_dt=search_start_date, changelist=True, search=issue_key,
                                         rel_filepath=self.dev_branch)

        files = set()
        max_revision = 0
        for log_entry in revisions:
            max_revision = max(max_revision, log_entry.revision)
            files = files.union({file
                                 for _, file in log_entry.changelist
                                 if Path(file).suffix in self.file_extensions})

        return max_revision, files

    def get_dependencies(self, revision_to_check=None, issue_key=None):
        if revision_to_check is None:
            if issue_key is None:
                logger.error('revision_to_check and issue_key both are None!')
                raise ValueError('must provide at least one argument with some value')
            else:
                revision_to_check, files = self.get_modified_files_for_issue(issue_key=issue_key)
        else:
            issue_key, files = self.get_modified_files_for_revision(revision=revision_to_check)

        logger.debug('files_found: ({}); {}'.format(len(files), files))
        logger.debug('revision_to_check: {}'.format(revision_to_check))
        logger.debug('issue_key = {}'.format(issue_key))

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
                if issue_key not in issues_in_revision:
                    for issue in issues_in_revision:
                        if issue in open_issues:
                            continue

                        issue_status = self.jira.issue(id=issue, fields='status').fields.status.name
                        logger.debug('{} {}'.format(issue, issue_status))

                        if issue_status not in self.statuses_to_ignore:
                            open_issue = Issue(issue, issue_status)
                            open_issues.add(open_issue)
            dependencies.append((Path(file).name, open_issues))
        return issue_key, dependencies

    def get_dependencies_as_dict(self, revision_to_check=None, issue_key=None):
        main_issue_key, dependencies = self.get_dependencies(revision_to_check=revision_to_check, issue_key=issue_key)
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
        'fallback': 'Summary for {}'.format(summary['main_issue_key']),
        'color': 'warning' if had_any_dependencies else 'good',
        'title': summary['main_issue_key'],
        'title_link': '{}/browse/{}'.format(jira_server, summary['main_issue_key']),
        'text': 'Summary for revision: {}'.format(summary['revision']),
        'fields': fields
    }
    # fixme: ugly?
    if summary['revision'] is None:
        del attachment['text']
    return [attachment]
