"""tests for passlib.pwhash -- (c) Assurance Technologies 2003-2009"""
#=========================================================
#imports
#=========================================================
from __future__ import with_statement
#core
import hashlib
from logging import getLogger
#site
#pkg
from passlib.tests.handler_utils import _HandlerTestCase
from passlib.tests.utils import enable_option
#module
log = getLogger(__name__)

#=========================================================
#PHPass Portable Crypt
#=========================================================
from passlib.hash import phpass

class PHPassTest(_HandlerTestCase):
    handler = phpass

    known_correct = (
        ('', '$P$7JaFQsPzJSuenezefD/3jHgt5hVfNH0'),
        ('compL3X!', '$P$FiS0N5L672xzQx1rt1vgdJQRYKnQM9/'),
        ('test12345', '$P$9IQRaTwmfeRo7ud9Fh4E2PdI0S3r.L0'), #from the source
        )

    known_invalid = (
        #bad char in otherwise correct hash
        '$P$9IQRaTwmfeRo7ud9Fh4E2PdI0S3r!L0',
        )

#=========================================================
#NTHASH for unix
#=========================================================
from passlib.hash import nthash

class NTHashTest(_HandlerTestCase):
    handler = nthash

    known_correct = (
        ('passphrase', '$3$$7f8fe03093cc84b267b109625f6bbf4b'),
        ('passphrase', '$NT$7f8fe03093cc84b267b109625f6bbf4b'),
    )

    known_invalid = (
        #bad char in otherwise correct hash
        '$3$$7f8fe03093cc84b267b109625f6bbfxb',
    )

#=========================================================
#EOF
#=========================================================