# encoding: utf-8
from setuptools import setup, find_packages

setup(
    name='ckanext-sentry',
    version='0.1.0',
    description='CKAN extension that forwards log messages to Sentry',
    long_description='Initializes the Sentry Python SDK and configures '
                     'LoggingIntegration to capture CKAN log messages as '
                     'breadcrumbs and events. All settings are configurable '
                     'via environment variables using ckanext-envvars.',
    url='https://github.com/cioos-siooc/ckanext-sentry',
    author='CIOOS',
    license='AGPL',
    classifiers=[
        'Development Status :: 4 - Beta',
        'License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)',
        'Programming Language :: Python :: 3',
    ],
    keywords='CKAN sentry logging monitoring',
    packages=find_packages(exclude=['contrib', 'docs', 'tests*']),
    namespace_packages=['ckanext'],
    install_requires=[],
    entry_points='''
        [ckan.plugins]
        sentry=ckanext.sentry.plugin:SentryPlugin
    ''',
)
