"""Grafana Alert launcher.

"""

import logging
import time

from grafana_alerts.alerting import AlertCheckerCoordinator

__author__ = 'Pablo Alcaraz'
__copyright__ = "Copyright 2015, Pablo Alcaraz"
# __credits__ = [""]
__license__ = "Apache Software License V2.0"


class Launcher:
    def launch(self):
        now = time.strftime("%c")
        configuration = Configuration()

        if not configuration.loglevel:
           setattr(configuration, "loglevel", logging.WARN)

        if not configuration.logfile:
           logging.basicConfig(level=configuration.loglevel)
        else:
           logging.basicConfig(filename=configuration.logfile, level=configuration.loglevel)

        logger = logging.getLogger(__name__)
        logger.info("================================================")
        logger.info("Execution started at %s " % now)
        logger.debug("Logging level set to %s" % configuration.loglevel)
        alert_checker = AlertCheckerCoordinator(configuration)
        alert_checker.check()


class Configuration:
    """Configuration."""

    def __init__(self):
        # TODO make sure the url finishes with '/' or requests could fail.
        self.grafana_url = 'http://localhost:3130/'
        self.grafana_token = ""
        self.email_from = "grafana-alert@localhost"
        self.smtp_server = "localhost"
        self.smtp_port = 25
        self.smtp_username = None
        self.smtp_password = None
        self.logfile = "/var/log/grafana_alerts.log"
        self.loglevel = logging.WARNING
        self.read_config()

    def read_config(self):
        try:
            with open("/etc/grafana_alerts/grafana_alerts.cfg", "r") as config_file:
                config = config_file.readlines()
                for line in config:
                    l = line.strip()
                    if len(l) == 0 or l.startswith('#'):
                        continue
                    k, v = [x.strip() for x in l.split('=', 1)]
                    if k == "loglevel":
                        v = v.upper()
                    setattr(self, k, v)
        except BaseException as e:
            raise RuntimeError("Error reading configuration /etc/grafana_alerts/grafana_alerts.cfg", e)
