# python-chi-operator

Want an operator CLI toolkit for Chameleon sites? This is it.

## Installation

![https://pypi.org/project/python-chi-operator/](https://img.shields.io/pypi/v/python-chi)

```shell
pip install python-chi-operator
```

## Usage

Most of these commands execute against a Chameleon OpenStack cloud deployment
and assume the requesting user has an admin role. Authentication credentials
are read from standard OpenStack environment variables, e.g. `OS_AUTH_URL`.
The easiest way to authenticate is to download an OpenRC file from the target
cloud site and source it in to your shell.

Use the `--help` at each layer to understand what command options are available:

```shell
chameleon --help
chameleon network --help
# and so on
```
