"""Refuse to push an image tag that the registry already has (D-167: a published version is
never pushed again, so its tag never moves). Used by release.yml just before the push.

Exit 0 only when the registry answers 404 for the tag. The tag existing (2xx), any other
status (a redirect included), a failed token request, a network error or a malformed answer
all exit 1: a lookup that cannot prove the tag is absent never lets the push go ahead.

Environment: IMAGE (registry host and repository, e.g. ghcr.io/horo86/portfolio-tracker: the
same variable the push uses, so the lookup can never check another repository), VERSION (the
image tag), ACTOR and TOKEN (the workflow's GITHUB_TOKEN; sent only to the registry, never
printed). REGISTRY_SCHEME is https unless a test sets it to http for a local fake registry.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer we did not expect: it fails the lookup instead of being followed
    to some other address whose 404 would look like "absent"."""

    def redirect_request(self, *args, **kwargs):
        return None


OPEN = urllib.request.build_opener(_NoRedirect).open

ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


def main() -> int:
    host, _, repository = os.environ["IMAGE"].partition("/")
    if not host or not repository:
        print("::error::IMAGE is not <registry>/<repository>: not pushing")
        return 1
    registry = f"{os.environ.get('REGISTRY_SCHEME', 'https')}://{host}"
    tag = os.environ["VERSION"]
    basic = base64.b64encode(f"{os.environ['ACTOR']}:{os.environ['TOKEN']}".encode()).decode()

    # The registry's own token for this repository, as `docker push` asks for it.
    scope = urllib.parse.quote(f"repository:{repository}:pull,push", safe=":/,")
    request = urllib.request.Request(
        f"{registry}/token?service={urllib.parse.urlparse(registry).hostname}&scope={scope}",
        headers={"Authorization": f"Basic {basic}"},
    )
    with OPEN(request, timeout=30) as answer:
        token = json.load(answer)["token"]
    if not isinstance(token, str) or not token:
        print("::error::the registry gave no token: not pushing")
        return 1

    request = urllib.request.Request(
        f"{registry}/v2/{repository}/manifests/{urllib.parse.quote(tag, safe='')}",
        method="HEAD",
        headers={"Authorization": f"Bearer {token}", "Accept": ACCEPT},
    )
    try:
        with OPEN(request, timeout=30) as answer:
            status = answer.status
    except urllib.error.HTTPError as error:
        if error.code == 404:
            print(f"{repository}:{tag} is not in the registry yet")
            return 0
        name = f"{repository}:{tag}"
        print(f"::error::registry lookup of {name} failed (HTTP {error.code}): not pushing")
        return 1
    print(
        f"::error::{repository}:{tag} is already in the registry (HTTP {status}): a published "
        "version is never pushed again; release a new patch version instead"
    )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # any failure means "not proven absent"
        print(f"::error::registry lookup failed ({type(error).__name__}): not pushing")
        sys.exit(1)
