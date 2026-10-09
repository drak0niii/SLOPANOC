"""Semantic resource shapes, shared with the Collector contract checks.

An arbitrary bounded string is not a deployment version or a cloud region.
Unsupported optional metadata is omitted rather than copied into telemetry.
"""
import re

PATTERNS={
    'service.name':r'^slopanoc$',
    'deployment.environment.name':r'^(local|development|test|staging|production)$',
    'slopanoc.telemetry_schema_version':r'^1$',
    'cloud.provider':r'^gcp$',
    'cloud.platform':r'^gcp_cloud_run$',
    'cloud.region':r'^[a-z]+(-[a-z]+)+[0-9]$',
    'slopanoc.git_sha':r'^[a-f0-9]{7,64}$',
    'service.version':r'^(v?[0-9]+([.][0-9]+){0,3}([-+][a-z0-9.-]+)?|[a-f0-9]{7,64}|unknown|unversioned)$',
    'slopanoc.release_id':r'^(release-[0-9]+|r[0-9]+|v?[0-9]+([.][0-9]+){0,3}|[a-f0-9]{7,64})$',
    'slopanoc.cloud_run_revision':r'^((rev|revision)-[0-9]+|slopanoc(-[a-z0-9]+)*-[0-9]{5}-[a-z0-9]{3})$',
}

def project(values):
    return {key:value for key,value in values.items()
        if key in PATTERNS and type(value) is str and len(value)<=64 and re.fullmatch(PATTERNS[key],value)}
