import sys
import jinja2
import logging
import logging.handlers
from logging import Formatter
import os
import time
import StringIO

import flask
from flask import current_app
from flask import g
from flask import url_for
from flask import Flask
from flask_restful import Resource, Api

import lnt
import lnt.server.db.v4db
import lnt.server.instance
import lnt.server.ui.filters
import lnt.server.ui.globals
import lnt.server.ui.views

import lnt.server.ui.regression_views
from lnt.server.ui.api import load_api_resources


class RootSlashPatchMiddleware(object):
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ['PATH_INFO'] == '':
            return flask.redirect(environ['SCRIPT_NAME'] + '/')(
                environ, start_response)
        return self.app(environ, start_response)

class Request(flask.Request):
    def __init__(self, *args, **kwargs):
        super(Request, self).__init__(*args, **kwargs)

        self.request_time = time.time()
        self.db = None
        self.testsuite = None

    def elapsed_time(self):
        return time.time() - self.request_time

    # Utility Methods

    def get_db(self):
        """
        get_db() -> <db instance>

        Get the active database and add a logging handler if part of the request
        arguments.
        """

        if self.db is None:
            echo = bool(self.args.get('db_log') or self.form.get('db_log'))

            self.db = current_app.old_config.get_database(g.db_name, echo=echo)

            # Enable SQL logging with db_log.
            #
            # FIXME: Conditionalize on an is_production variable.
            if echo:
                g.db_log = StringIO.StringIO()
                logger = logging.getLogger("sqlalchemy")
                logger.addHandler(logging.StreamHandler(g.db_log))

        return self.db

    def get_testsuite(self):
        """
        get_testsuite() -> server.db.testsuite.TestSuite

        Get the active testsuite.
        """

        if self.testsuite is None:
            testsuites = self.get_db().testsuite
            if g.testsuite_name not in testsuites:
                flask.abort(404)

            self.testsuite = testsuites[g.testsuite_name]

        return self.testsuite

    def close(self):
        db = getattr(self, 'db', None)
        if db is not None:
            db.close()
        return super(Request, self).close()


class App(flask.Flask):
    @staticmethod
    def create_with_instance(instance):
        # Construct the application.
        app = App(__name__)

        # Register additional filters.
        create_jinja_environment(app.jinja_env)

        # Set up strict undefined mode for templates.
        app.jinja_env.undefined = jinja2.StrictUndefined

        # Load the application configuration.
        app.load_config(instance)

        # Load the application routes.
        app.register_module(lnt.server.ui.views.frontend)

        # Load the flaskRESTful API.
        app.api = Api(app)
        load_api_resources(app.api)

        return app

    @staticmethod
    def create_standalone(config_path):
        instance = lnt.server.instance.Instance.frompath(config_path)
        app =  App.create_with_instance(instance)
        app.start_file_logging()
        return app
    
    def __init__(self, name):
        super(App, self).__init__(name)
        self.start_time = time.time()
        # Override the request class.
        self.request_class = Request

        # Store a few global things we want available to templates.
        self.version = lnt.__version__

        # Inject a fix for missing slashes on the root URL (see Flask issue
        # #169).
        self.wsgi_app = RootSlashPatchMiddleware(self.wsgi_app)
        
        self.lnt_logger = logging.getLogger('LNT')
        self.lnt_logger.setLevel(logging.DEBUG)
        
    def load_config(self, instance):
        self.instance = instance
        self.old_config = self.instance.config

        self.jinja_env.globals.update(
            app=current_app,
            old_config=self.old_config)

        # Set the application secret key.
        self.secret_key = self.old_config.secretKey

    def start_file_logging(self):
        """Start server production logging.  At this point flask already logs
        to stderr, so just log to a file as well.

        """
        # Print to screen.
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        self.lnt_logger.addHandler(ch)
        
        # Log to mem for the /log view.
        h = logging.handlers.MemoryHandler(1024 * 1024)
        h.setLevel(logging.DEBUG)
        self.lnt_logger.addHandler(h)
        self.logger.addHandler(h)
        self.old_config.mem_logger = h
        
        if not self.debug:
            LOG_FILENAME = "lnt.log"
            try:    
                rotating = logging.handlers.RotatingFileHandler(
                    LOG_FILENAME, maxBytes=1048576, backupCount=5)
                rotating.setFormatter(Formatter(
                    '%(asctime)s %(levelname)s: %(message)s '
                    '[in %(pathname)s:%(lineno)d]'
                ))
                rotating.setLevel(logging.DEBUG)
                self.lnt_logger.addHandler(rotating)                
            except (OSError, IOError) as e:
                print >> sys.stderr, "Error making log file", LOG_FILENAME, str(e)
                print >> sys.stderr, "Will not log to file."
            else:
                self.logger.info("Started file logging.")
                print "Logging to :", LOG_FILENAME
            

def create_jinja_environment(env=None):
    """
    create_jinja_environment([env]) -> jinja2.Environment

    Create (or modify) a new Jinja2 environment suitable for rendering the LNT
    templates.
    """

    if env is None:
        env = jinja2.Environment(loader=jinja2.PackageLoader(
                'lnt.server.ui', 'templates'))
    lnt.server.ui.globals.register(env)
    lnt.server.ui.filters.register(env)

    return env
