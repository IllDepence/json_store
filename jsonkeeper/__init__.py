""" JSONkeeper

    A flask web application for storing JSON documents; with some special
    functions for JSON-LD.
"""

import atexit
import firebase_admin
from flask import Flask, jsonify
from pyld import jsonld
from werkzeug.exceptions import default_exceptions, HTTPException
from werkzeug.routing import BaseConverter
from jsonkeeper.config import Cfg
from jsonkeeper.subroutines import add_CORS_headers, collect_garbage, log
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger


__version__ = '1.0.0'


def create_app(**kwargs):
    app = Flask(__name__)
    with app.app_context():
        app.cfg = Cfg()
        startup_msg = 'starting'
        if kwargs:
            app.testing = True
            app.cfg.set_debug_config(**kwargs)
            startup_msg += ' in testing mode with kwargs:'
            for k, v in kwargs.items():
                startup_msg += ' {}={}'.format(k, v)
        log(startup_msg)

        class RegexConverter(BaseConverter):
            """ Make it possible to distinguish routes by <regex("[exampl]"):>.

                https://stackoverflow.com/a/5872904
            """

            def __init__(self, url_map, *items):
                super(RegexConverter, self).__init__(url_map)
                self.regex = items[0]

        app.url_map.converters['regex'] = RegexConverter

        if app.cfg.use_frbs():
            log('using Firebase')
            cred = firebase_admin.credentials.Certificate(app.cfg.frbs_conf())
            firebase_admin.initialize_app(cred)
        else:
            log('NOT using Firebase')
        app.config['SQLALCHEMY_DATABASE_URI'] = app.cfg.db_uri()
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

        from jsonkeeper.models import db
        db.init_app(app)
        db.create_all()

        jsonld.set_document_loader(jsonld.requests_document_loader(timeout=7))

        for code in default_exceptions.keys():
            """ Make app return exceptions in JSON form. Also add CORS headers.

                Based on http://flask.pocoo.org/snippets/83/ but updated to use
                register_error_handler method.
                Note: this doesn't seem to work for 405 Method Not Allowed in
                      an Apache + gnunicorn setup.
            """

            @app.errorhandler(code)
            def make_json_error(error):
                resp = jsonify(message=str(error))
                resp.status_code = (error.code
                                    if isinstance(error, HTTPException)
                                    else 500)
                return add_CORS_headers(resp)

        @app.after_request
        def set_response_headers(response):
            response.headers['Cache-Control'] = ('private, no-store, no-cache,'
                                                 ' must-revalidate')
            # response.headers['Pragma'] = 'no-cache'
            # response.headers['Expires'] = '0'
            return response

        from jsonkeeper.views import jk
        app.register_blueprint(jk)

        if app.cfg.garbage_collection_interval() > 0:
            log('initializing garbage collection')
            scheduler = BackgroundScheduler()
            scheduler.start()
            scheduler.add_job(
                func=collect_garbage,
                trigger=IntervalTrigger(
                            seconds=app.cfg.garbage_collection_interval()),
                id='garbage_collection_job',
                name='collect garbage according to interval set in config',
                replace_existing=True)
            atexit.register(lambda: scheduler.shutdown())

        return app
