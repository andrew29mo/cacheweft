"""Public observations of the same regression scenarios as unit qualification."""
import argparse
import json
import os
from pathlib import Path
import tempfile
from tests.fixture import Origin
from tests.review_fixture import admission,publication


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
    with tempfile.TemporaryDirectory(prefix='cw-review-cases-',dir='/tmp') as tmp:
        base=Path(tmp);os.chmod(base,0o755);origin=Origin(base/'tls')
        try:
            cases=[admission(base/'admission',origin),publication(base/'single',origin),
                   publication(base/'begin',origin,kind='writer',shared=True),publication(base/'commit',origin,shared=True)]
        finally:origin.release.set();origin.close()
    Path(a.output).write_text(json.dumps(dict(cases=cases,cleanup=True),indent=2)+'\n')

if __name__=='__main__':main()
