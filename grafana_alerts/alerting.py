"""Alerts"""
import urllib2
import json
import re
import logging

import jmespath

from grafana_alerts.reporting import AlertEvaluationResult, MailAlertReporter

__author__ = 'Pablo Alcaraz'
__copyright__ = "Copyright 2015, Pablo Alcaraz"
# __credits__ = [""]
__license__ = "Apache Software License V2.0"

_GRAFANA_URL_PATH_OBTAIN_DASHBOARDS = 'api/search?limit=10&query=&tag=monitored'
_GRAFANA_URL_PATH_DASHBOARD = 'api/dashboards/db/{slug}'
_GRAFANA_URL_PATH_OBTAIN_METRICS = 'api/datasources/proxy/1/render'

logger = logging.getLogger(__name__)

class NotMonitoreableDashboard(RuntimeError):
    def __init__(self, message):
        self.message = message

class AlertCheckerCoordinator:
    """Entry point to the alert checking module."""

    def __init__(self, configuration):
        self.configuration = configuration
        self.alert_reporter = MailAlertReporter(email_from=self.configuration.email_from,
                                                smtp_server=self.configuration.smtp_server,
                                                smtp_port=self.configuration.smtp_port,
                                                email_username=self.configuration.smtp_username,
                                                email_password=self.configuration.smtp_password)

    def check(self):
        """Check if there is something to report"""
        # Get all the dashboards to use for checking
        scanner = DashboardScanner(self.configuration.grafana_url, self.configuration.grafana_token)
        dashboard_data_list = scanner.obtain_dashboards()
        logger.debug("Dashboard data list: %s" % dashboard_data_list)
        for d in dashboard_data_list:
            try:
                logger.info("Processing Dashboard: %s" % d['title'])
                # {u'slug': u'typrod-storage', u'tags': [], u'isStarred': False, u'id': 4, u'title': u'TyProd Storage'}
                dashboard = Dashboard(self.configuration.grafana_url, self.configuration.grafana_token, d['title'], d['uri'], d['tags'])
                alert_checkers = dashboard.obtain_alert_checkers()
                logger.debug("Alert Checkers: %s" % alert_checkers)

                # For each set of alert checkers, evaluate them
                for alert_checker in alert_checkers:
                    alert_checker.check()
                    reported_alerts = alert_checker.calculate_reported_alerts()
                    # for each set of reported alerts, report whatever is best
                    self.alert_reporter.report(reported_alerts)
            except NotMonitoreableDashboard as e:
                logger.exception("Dashboard %s cannot be monitored. Reason: %s" % (d['title'], e.message))
                continue


class AlertChecker:
    """Command to check metrics."""

    def __init__(self, grafana_url, grafana_token, title, grafana_targets):
        self.grafana_url = grafana_url
        self.grafana_token = grafana_token
        self.title = title
        self.grafana_targets = grafana_targets
        self.checkedExecuted = False
        self.responses = []
        self.alert_conditions = None

    def set_alert_conditions(self, alert_conditions):
        """Alerts conditions are composed by an array of elements.
        each element is an array like:

            [["interval1","status1","alert destination1","short description","long description"],
            ["interval2","status2","alert destination2","short description","long description"],
            ["intervaln","statusN","alert destinationN","short description","long description"]]

        interval: string representing an interval like:
            "x<=0": -infinite < x <= 0
            "0<=x<50": [0;50)
            "50<=x": 50 <= x < infinite

        status: "normal", "warning", "critical"

        alert destination: 1 or more emails separated by ","

        example:
            [["50<=x<=100", "normal", "p@q.com"],
            ["50<x<=35", "warning", "p@q.com"],
            ["35<=x", "critical", "p@q.com"]]
        """
        # TODO verify alert conditions are valid.
        self.alert_conditions = alert_conditions

    def check(self):
        """get metrics from grafana server"""
        for grafana_target in self.grafana_targets:
            if not hasattr(grafana_target, 'hide') or not grafana_target['hide']:
                logger.debug("Grafana Target: %s" % grafana_target)
                target = grafana_target['target']
                post_parameters = "target={target}&from=-60s&until=now&format=json&maxDataPoints=100".format(
                    target=target)

                if not self.grafana_token:
                    headers={"Accept": "application/json"}
                else:
                    headers={"Accept": "application/json",
                             "Authorization": "Bearer " + self.grafana_token}

                logger.info("curl -XPOST '%s' --data '%s'" % (self.grafana_url + _GRAFANA_URL_PATH_OBTAIN_METRICS, post_parameters))
                request = urllib2.Request(self.grafana_url + _GRAFANA_URL_PATH_OBTAIN_METRICS,
                                          data=post_parameters,
                                          headers=headers)

                contents = urllib2.urlopen(request).read()
                self.responses.append(json.loads(contents))
        self.checkedExecuted = True

    def calculate_reported_alerts(self):
        alert_evaluation_result_list = []
        if not self.checkedExecuted:
            raise RuntimeError("method check() was not invoked, therefore there is nothing to report about. Fix it.")

        if self.alert_conditions is None:
            raise RuntimeError(
                "method set_alert_conditions() was not invoked, therefore there is nothing to report about. Fix it.")

        for response in self.responses:
            # A grafana response could cover several sources/hosts.
            for source in response:
                alert_evaluation_result = AlertEvaluationResult(title=self.title, target=source['target'])
                # calculate 'x': for now 'x' is the average of all not null data points.
                data = [m[0] for m in source['datapoints'] if m[0] is not None]
                if len(data) > 0:
                    x = float(sum(data)) / len(data)
                else:
                    x = float('nan')
                alert_evaluation_result.set_current_value(x)

                # evaluate all the alert conditions and create a current alert status.
                for alert_condition in self.alert_conditions:
                    condition = alert_condition[0]
                    activated = eval(condition)
                    alert_evaluation_result.add_alert_condition_result(name=alert_condition[1], condition=condition,
                                                                       activated=activated,
                                                                       alert_destination=alert_condition[2],
                                                                       title=self.title)

                alert_evaluation_result_list.append(alert_evaluation_result)

        return alert_evaluation_result_list


class DashboardScanner:
    """Provides access to grafana dashboards"""
    def __init__(self, grafana_url, grafana_token):
        self.grafana_url = grafana_url
        self.grafana_token = grafana_token

    def obtain_dashboards(self):
        if not self.grafana_token:
            headers={"Accept": "application/json"}
        else:
            headers={"Accept": "application/json",
                     "Authorization": "Bearer " + self.grafana_token}

        logger.info("curl '%s'" % (self.grafana_url + _GRAFANA_URL_PATH_OBTAIN_DASHBOARDS))
        request = urllib2.Request(self.grafana_url + _GRAFANA_URL_PATH_OBTAIN_DASHBOARDS,
                                  headers=headers)

        contents = urllib2.urlopen(request).read()
        logger.debug("Dashboard Search Result: %s" % contents)
        data = json.loads(contents)
        return data


class Dashboard:
    def __init__(self, grafana_url, grafana_token, title, slug, tags):
        self.grafana_url = grafana_url
        self.grafana_token = grafana_token
        self.title = title
        self.slug = re.sub('^db/', '', slug)
        self.tags = tags

    def obtain_alert_checkers(self):
        """check metrics and return a list of triggered alerts."""
        dashboard_info = self._obtain_dashboard_rows()
        alert_checkers = self._create_alert_checkers(dashboard_info)
        return alert_checkers

    def _obtain_dashboard_rows(self):
        """Get a list of dashboard rows."""
        if not self.grafana_token:
            headers={"Accept": "application/json"}
        else:
            headers={"Accept": "application/json",
                     "Authorization": "Bearer " + self.grafana_token}

        logger.info("curl '%s'" % (self.grafana_url + _GRAFANA_URL_PATH_DASHBOARD.format(slug=self.slug)))
        request = urllib2.Request(self.grafana_url + _GRAFANA_URL_PATH_DASHBOARD.format(slug=self.slug),
                                  headers=headers)

        contents = urllib2.urlopen(request).read()
        # Fix \n inside json values.
        contents = contents.replace('\r\n', '\\r\\n').replace('\n', '\\n')
        logger.debug("Dashboard rows: %s" % contents)
        try:
            data = json.loads(contents)
            dashboard = jmespath.search('dashboard.rows[*].panels[*]', data)
            return dashboard
        except ValueError:
            raise NotMonitoreableDashboard(
                "The definition of dashboard {title} does not look like valid json.".format(title=self.title))

    def _create_alert_checkers(self, dashboard_info):
        """check metrics and return a list of alerts to evaluate.
        :return AlertChecker list of all the metrics
        """
        alert_checkers = []
        for dashboard_row in dashboard_info:
            logger.debug("Dashboard row: %s" % dashboard_row)
            # creates alert checkers for each panel in the row.
            # TODO add alert checker creation to a builder dashboard_row2alert_checker_list.

            alert_conditions = []  # map of alert conditions( text -> alert parameters)
            for panel in dashboard_row:
                logger.debug("Panel: %s" % panel)
                logger.info("Checking Panel: %s is type: %s" % (panel['title'], panel['type']))

                if panel['type'] == "graph":
                    alert_checker = AlertChecker(self.grafana_url, self.grafana_token, panel['title'], panel['targets'])
                    alert_checkers.append(alert_checker)

                elif panel['type'] == "singlestat":
                    alert_checker = AlertChecker(self.grafana_url, self.grafana_token, panel['title'], panel['targets'])
                    alert_checkers.append(alert_checker)

                elif panel['type'] == "text":
                    if panel['title'] == 'alerts':
                        # read alert parameters to apply to all the alert checkers of this dashboard.
                        for line in panel['content'].splitlines():
                            # TODO replace alert_definition_list for an object
                            alert_definition_list = [s.strip() for s in line.split(';')]
                            if len(alert_definition_list) > 1:
                                alert_conditions.append(alert_definition_list)
                else:
                    logger.warning("Unknown type %s. Ignoring." % panel['type'])

            if len(alert_conditions) > 0:
                # There are alert conditions, add them to all the alert_checkers.
                for alert_checker in alert_checkers:
                    alert_checker.set_alert_conditions(alert_conditions)
        return alert_checkers
