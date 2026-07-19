#!/usr/env/python3

# imports
import requests

# class definitions
class SocrataClient:
    # SocrataClient is a client for interacting with the Socrata API
    def __init__(self, domain, app_token):
        self.domain = domain
        self.app_token = app_token
        self.session = requests.Session()

