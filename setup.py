import os.path
from nagato import __version__ as version

try:
    from setuptools import find_packages, setup
except ImportError:
    from ez_setup import use_setuptools
    use_setuptools()
    from setuptools import find_packages, setup


def readme():
    try:
        with open(os.path.join(os.path.dirname(__file__), 'README.rst')) as f:
            return f.read()
    except (IOError, OSError):
        return ''


setup(
    name='nagato',
    version=version,
    description='Bypass korean firewall(warning.or.kr)',
    long_description=readme(),
    url='https://github.com/kjwon15/nagato',
    author='Kjwon15',
    author_email='kjwonmail' '@' 'gmail.com',
    entry_points={
        'console_scripts': [
            'nagato = nagato:main'
        ]
    },
    license='GPLv3 or later',
    py_modules=['nagato'],
)
