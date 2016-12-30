Nagato
======

local proxy for bypassing warning.or.kr


Install
-------

You can install, upgrade, remove nagato using ``pip``:

.. code-block:: console

   $ pip install --user nagato
   $ pip install --user --upgrade nagato
   $ pip uninstall nagato

Usage
-----

You just execute nagato in your terminal.

.. code-block:: console

   $ nagato -H localhost -p 8080 -vv

then, you should be config your browser's config to use nagato proxy: localhost:8080

You can see help message with this command.

.. code-block:: console

   $ nagato --help
