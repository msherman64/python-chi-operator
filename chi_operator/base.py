import chi

import logging


class BaseCommand:
    def __init__(self):
        self.session = chi.session()
        self.log = logging.getLogger(__name__)

    def blazar(self):
        return chi.blazar(session=self.session)

    def glance(self):
        return chi.glance(session=self.session)

    def gnocchi(self):
        return chi.gnocchi(session=self.session)

    def ironic(self):
        return chi.ironic(session=self.session)

    def keystone(self):
        return chi.keystone(session=self.session)

    def neutron(self):
        return chi.neutron(session=self.session)

    def nova(self):
        return chi.nova(session=self.session)
