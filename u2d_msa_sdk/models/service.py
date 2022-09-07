# -*- coding: utf-8 -*-
__version__ = '0.0.3'

from typing import Dict, List, Optional
from pydantic import BaseModel, typing

from u2d_msa_sdk.models.health import MSAHealthDefinition


class MSAServiceDefinition(BaseModel):
    name: str = "MSA SDK Service"
    version: str = "0.0.0"
    host: str = "127.0.0.1"
    port: int = 8090
    tags: List[str] = []
    metadata: Optional[Dict]
    healthdefinition: MSAHealthDefinition = MSAHealthDefinition()
    uvloop: bool = True
    sysrouter: bool = True
    servicerouter: bool = True
    starception: bool = True
    cors: bool = True
    redirect: bool = False
    gzip: bool = False
    session: bool = True
    csrf: bool = True
    msgpack: bool = False
    instrument: bool = True
    static: bool = True
    templates: bool = True
    pages: bool = True
    graphql: bool = False
    context: bool = False
    pagination: bool = False
    profiler: bool = False
    profiler_output_type: str = "html"  # text or html
    profiler_single_calls: bool = True
    timing: bool = False
    limiter: bool = False
    scheduler: bool = True
    scheduler_poll_millis: int = 500
    db: bool = True
    db_crud: bool = True
    db_meta_drop: bool = False
    db_meta_create: bool = True
    db_url: str = 'sqlite+aiosqlite:///msa_sdk.db'
