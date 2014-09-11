Nagato
======

local proxy for bypassing warning.or.kr


Install
-------

You can install, upgrade, remove nagato using ``pip``:

.. code-block:: console

   $ pip install git+git://github.com/kjwon15/nagato.git
   $ pip install --upgrade git+git://github.com/kjwon15/nagato.git
   $ pip uninstall git+git://github.com/kjwon15/nagato.git

Usage
-----

You just execute nagato in your terminal.

.. code-block:: console

   $ nagato -H localhost -p 8080 -vv --logfile /tmp/nagato.log

then, you should be config your browser's config to use nagato proxy: localhost:8080

You can see help message with this command.

.. code-block:: console

   $ nagato --help
