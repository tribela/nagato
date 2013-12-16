import os.path

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
    description='Bypass korean firewall(warning.or.kr)',
    long_description=readme(),
    url='http://kjwon15.ufree.org/',
    author='Kjwon15',
    author_email='kjwonmail' '@' 'gmail.com',
    entry_points={
        'console_scripts': [
            'nagato = nagato:main'
        ]
    },
    license='GPLv3 or later',
    packages=find_packages(),
)
