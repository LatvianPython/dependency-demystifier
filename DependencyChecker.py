from svn import local
import re
from pathlib import Path
from jira import JIRA
from collections import namedtuple
import time

Issue = namedtuple(typename='issue', field_names=['issue_key', 'status'])


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
        return self.issue_regex.findall(log_message)

    def get_dependencies(self, revision_to_check):
        log_entry = next(self.svn.log_default(revision_from=revision_to_check, revision_to=revision_to_check,
                                              limit=1, changelist=True))

        files = [file for _, file in log_entry.changelist if Path(file).suffix in self.file_extensions]

        try:  # fixme: provide actual solution to this
            main_issue_key = self.get_issue_keys(log_message=log_entry.msg).pop()
        except IndexError:
            main_issue_key = None

        dependencies = []
        for file in files:
            # fixme: check if should use revision_to=revision_to_check, most likely: yes
            revisions = self.svn.log_default(rel_filepath=file, limit=self.max_checked_revisions)

            open_issues = set()
            for revision in revisions:
                issues_in_revision = self.get_issue_keys(log_message=revision.msg)
                if main_issue_key not in issues_in_revision:
                    for issue in issues_in_revision:
                        issue_status = self.jira.issue(id=issue, fields='status').fields.status.name

                        if issue_status not in self.statuses_to_ignore:
                            open_issues.add(Issue(issue, issue_status))
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

    had_dependencies = any(len(issues) > 0 for _, issues in summary['files'].items())

    attachment = {
        'fallback': 'fallback',  # todo: provide actual fallback
        'color': 'warning' if had_dependencies else 'good',
        'title': summary['main_issue_key'],
        'title_link': '{}/browse/{}'.format(jira_server, summary['main_issue_key']),
        'text': 'Summary for revision: {}'.format(summary['revision']),
        'fields': fields,
        'ts': time.time()
    }
    return [attachment]
