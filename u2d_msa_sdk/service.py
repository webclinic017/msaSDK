# -*- coding: utf-8 -*-
__version__ = '0.0.3'

import os
import time
from typing import List, Optional

import uvloop
from fastapi.encoders import jsonable_encoder
from fastapi.responses import ORJSONResponse
from fastapi_pagination import add_pagination
from fastapi_users.password import PasswordHelper
from fastapi_utils.api_settings import APISettings
from fastapi_utils.timing import add_timing_middleware
from loguru import logger
from msgpack_asgi import MessagePackMiddleware
from passlib.context import CryptContext
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel
from starception import StarceptionMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette_context import plugins
from starlette_context.middleware import RawContextMiddleware
from starlette_wtf import CSRFProtectMiddleware
from strawberry import schema

from u2d_msa_sdk.db.crud import MSASQLModelCrud
from u2d_msa_sdk.models.health import MSAHealthMessage
from u2d_msa_sdk.models.service import MSAServiceDefinition, MSAHealthDefinition
from u2d_msa_sdk.msaapi import MSAFastAPI
from u2d_msa_sdk.router.system import sys_router
from u2d_msa_sdk.security import getMSASecurity
from u2d_msa_sdk.utils import healthcheck as health
from u2d_msa_sdk.utils.logger import init_logging
from u2d_msa_sdk.utils.profiler import MSAProfilerMiddleware
from u2d_msa_sdk.utils.scheduler import MSATimers, MSAScheduler
from u2d_msa_sdk.utils.sysinfo import get_sysinfo, MSASystemInfo

security_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
password_helper = PasswordHelper(security_context)
security = getMSASecurity()


class MSAServiceStatus(BaseModel):
    name: Optional[str] = "None"
    healthy: Optional[str] = "None"
    message: Optional[str] = "None"


class MSAOpenAPIInfo(BaseModel):
    name: str = "MSA SDK Service"
    version: str = "0.0.0"
    url: str = "/openapi.json"
    tags: Optional[List[str]] = None


def getSecretKey():
    ret_key: str = os.getenv("SECRET_KEY_TOKEN",
                             "u2dmsaservicex_#M8A{1o3Bd?<ipwt^K},Z)OE<Fkj-X9IILWq|Cf`Y:HFI~&2L%Ion3}+p{T%")
    return ret_key


def getSecretKeySessions():
    ret_key: str = os.getenv("SECRET_KEY_SESSIONS",
                             "u2dmsaserviceeP)zg5<g@4WJ0W8'?ad!T9UBvW1z2k|y~|Pgtewv=H?GY_Q]t~-~UUe'pJ0V[>!<)")
    return ret_key


def getSecretKeyCSRF():
    ret_key: str = os.getenv("SECRET_KEY_CSRF",
                             "u2dmsaservicee_rJM'onkEV1trD=I7dci$flB)aSNW+raL4j]Ww=n~_BRg35*3~(E.>rx`1aTw:s")
    return ret_key


def getAllowedOrigins() -> List[str]:
    origins: List[str] = [os.getenv("ALLOWED_ORIGINS", "*")]
    return origins


def getAllowedMethods() -> List[str]:
    methods: List[str] = [os.getenv("ALLOWED_METHODS", "*")]
    return methods


def getAllowedHeaders() -> List[str]:
    headers: List[str] = [os.getenv("ALLOWED_HEADERS", "*")]
    return headers


def getAllowedCredentials() -> bool:
    cred: bool = os.getenv("ALLOWED_CREDENTIALS", True)
    return cred


class MSAApp(MSAFastAPI):
    def __init__(
            self,
            settings: APISettings,
            service_definition: MSAServiceDefinition = MSAServiceDefinition(),
            timers: MSATimers = None,
            sql_models: List[SQLModel] = None,
            *args,
            **kwargs
    ) -> None:
        # call super class __init__
        super().__init__(*args, **settings.fastapi_kwargs)
        self.logger = logger
        init_logging()
        self.settings = settings
        self.service_definition: MSAServiceDefinition = service_definition
        self.timers: MSATimers = timers
        self.healthdefinition: MSAHealthDefinition = self.service_definition.healthdefinition
        self.limiter: Limiter = None
        self.db_engine: AsyncEngine = None
        self.sql_models: List[SQLModel] = sql_models
        self.sql_cruds: List[MSASQLModelCrud] = []
        self.scheduler: MSAScheduler = None

        if self.service_definition.uvloop:
            uvloop.install()
        self.healthcheck: health.MSAHealthCheck = None

        self.ROOTPATH = os.path.join(os.path.dirname(__file__))

        if self.service_definition.db:
            self.logger.info("DB - Init: " + self.service_definition.db_url)
            self.db_engine = create_async_engine(self.service_definition.db_url, future=True)
            if self.service_definition.db_crud and self.sql_models:
                self.logger.info("DB - Register/CRUD SQL Models: " + str(self.sql_models))
                # register all Models and the crud for them
                for model in self.sql_models:
                    new_crud: MSASQLModelCrud = MSASQLModelCrud(model=model, engine=self.db_engine).register_crud()
                    self.include_router(new_crud.router)
                    self.sql_cruds.append(new_crud)
        else:
            self.logger.info("Excluded DB")

        if self.service_definition.graphql:
            self.logger.info("Init Graphql")
            from strawberry.fastapi import GraphQLRouter
            self.graphql_app: GraphQLRouter = None
            self.graphql_schema: schema = None
        else:
            self.logger.info("Excluded Graphql")

        if self.healthdefinition.enabled:
            self.logger.info("Init Healthcheck")
            self.healthcheck = health.MSAHealthCheck(
                healthdefinition=self.healthdefinition,
                host=self.service_definition.host,
                port=self.service_definition.port
            )
            self.logger.info("Start Healthcheck Thread")
            self.healthcheck.start()
            self.add_api_route(self.healthdefinition.path, self.get_healthcheck,
                               response_model=MSAHealthMessage,
                               tags=["service"])
        else:
            self.logger.info("Excluded Healthcheck")

        if self.service_definition.sysrouter:
            self.logger.info("Include Sysrouter")
            self.include_router(sys_router)
        else:
            self.logger.info("Excluded Sysrouter")

        if self.service_definition.starception:
            self.logger.info("Add Middleware Starception")
            self.add_middleware(StarceptionMiddleware)
        else:
            self.logger.info("Excluded Middleware Starception")

        if self.service_definition.cors:
            self.logger.info("Add Middleware CORS")
            self.add_middleware(CORSMiddleware, allow_origins=getAllowedOrigins(),
                                allow_credentials=getAllowedCredentials(),
                                allow_methods=getAllowedMethods(),
                                allow_headers=getAllowedHeaders(), )
        else:
            self.logger.info("Excluded Middleware CORS")

        if self.service_definition.redirect:
            self.logger.info("Add Middleware Redirect")
            self.add_middleware(HTTPSRedirectMiddleware)
        else:
            self.logger.info("Excluded Middleware Redirect")

        if self.service_definition.gzip:
            self.logger.info("Add Middleware GZip")
            self.add_middleware(GZipMiddleware)
        else:
            self.logger.info("Excluded Middleware GZip")

        if self.service_definition.session:
            self.logger.info("Add Middleware Session")
            self.add_middleware(SessionMiddleware, secret_key=getSecretKeySessions())
        else:
            self.logger.info("Excluded Middleware Session")

        if self.service_definition.csrf:
            self.logger.info("Add Middleware CSRF")
            self.add_middleware(CSRFProtectMiddleware, csrf_secret=getSecretKeyCSRF())
        else:
            self.logger.info("Excluded Middleware CSRF")

        if self.service_definition.msgpack:
            self.logger.info("Add Middleware MSGPack")
            self.add_middleware(MessagePackMiddleware)
        else:
            self.logger.info("Excluded Middleware MSGPack")

        if self.service_definition.context:
            self.logger.info("Add Middleware Context")
            self.add_middleware(RawContextMiddleware, plugins=(
                plugins.RequestIdPlugin(),
                plugins.CorrelationIdPlugin()
            ))
        else:
            self.logger.info("Excluded Middleware Context")

        if self.service_definition.profiler:
            self.logger.info("Add Middleware Profiler")
            self.add_middleware(MSAProfilerMiddleware,
                                profiler_output_type=self.service_definition.profiler_output_type,
                                track_each_request=self.service_definition.profiler_single_calls,
                                msa_app=self)
        else:
            self.logger.info("Excluded Middleware Profiler")

        if self.service_definition.timing:
            self.logger.info("Add Middleware Timing")
            add_timing_middleware(self, record=self.logger.info, prefix="app", exclude="untimed")
        else:
            self.logger.info("Excluded Middleware Timing")

        if self.service_definition.limiter:
            self.logger.info("Add Limiter Engine")
            self.limiter = Limiter(key_func=get_remote_address)
            self.state.limiter = self.limiter
            self.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        else:
            self.logger.info("Excluded Limiter Engine")

        if self.service_definition.servicerouter:
            self.logger.info("Include Servicerouter")
            self.add_api_route("/status", self.get_services_status, tags=["service"],
                               response_model=MSAServiceStatus)
            self.add_api_route("/definition", self.get_services_definition, tags=["service"],
                               response_model=MSAServiceDefinition)
            self.add_api_route("/settings", self.get_services_settings, tags=["service"])
            self.add_api_route("/schema", self.get_services_openapi_schema, tags=["openapi"])
            self.add_api_route("/info", self.get_services_openapi_info, tags=["openapi"],
                               response_model=MSAOpenAPIInfo)
        else:
            self.logger.info("Excluded Servicerouter")

        if self.service_definition.static or self.service_definition.pages:
            self.logger.info("Mount MSAStatic")
            self.mount("/msastatic", StaticFiles(directory="msastatic"), name="msastatic")
        else:
            self.logger.info("Excluded MSAStatic")

        if self.service_definition.pagination:
            self.logger.info("Add Pagination Engine")
            add_pagination(self)
        else:
            self.logger.info("Excluded Pagination Engine")

        if self.service_definition.templates or self.service_definition.pages:
            self.logger.info("Init Jinja Template Engine")
            self.templates = Jinja2Templates(directory="msatemplates")
        else:
            self.logger.info("Excluded Jinja Template Engine")

        if self.service_definition.pages:
            self.logger.info("Add Pages Router")
            self.add_api_route("/", self.index_page, tags=["pages"], response_class=HTMLResponse)
            self.add_api_route("/testpage", self.testpage, tags=["pages"], response_class=HTMLResponse)
            self.add_api_route("/monitor", self.monitor, tags=["pages"], response_class=HTMLResponse)
            self.add_api_route("/profiler", self.profiler, tags=["pages"], response_class=HTMLResponse)
            self.add_api_route("/monitor_inline", self.monitor_inline, tags=["pages"], response_class=HTMLResponse)
        else:
            self.logger.info("Excluded Pages Router")

        if self.service_definition.instrument:
            self.logger.info("Prometheus Instrument and Expose App")
            Instrumentator().instrument(app=self).expose(app=self, include_in_schema=True, tags=["service"],
                                                         response_class=HTMLResponse)
        else:
            self.logger.info("Excluded Prometheus Instrument and Expose")

        self.add_event_handler("shutdown", self.shutdown_event)
        self.add_event_handler("startup", self.startup_event)

        if self.service_definition.scheduler and self.timers:
            if time.daylight:
                offsetHour = time.altzone / 3600
            else:
                offsetHour = time.timezone / 3600
            tz: str = 'Etc/GMT%+d' % offsetHour
            self.scheduler = MSAScheduler(jobs=self.timers.timer_jobs, local_time_zone=tz,
                                          poll_millis=self.service_definition.scheduler_poll_millis)
            await self.scheduler.run_timers()

    async def startup_event(self):
        self.logger.info("MSA SDK Internal Startup Event")
        if self.service_definition.db:
            async with self.db_engine.begin() as conn:
                if self.service_definition.db_meta_drop:
                    self.logger.info("DB - Drop Meta All: " + self.service_definition.db_url)
                    await conn.run_sync(SQLModel.metadata.drop_all)
                if self.service_definition.db_meta_create:
                    self.logger.info("DB - Create Meta All: " + self.service_definition.db_url)
                    await conn.run_sync(SQLModel.metadata.create_all)

    async def shutdown_event(self):
        self.logger.info("MSA SDK Internal Shutdown Event")
        if self.service_definition.db:
            self.logger.info("DB - Dispose Connections: " + self.service_definition.db_url)
            await self.db_engine.dispose()

    async def init_graphql(self, strawberry_schema: schema):
        if self.service_definition.graphql:
            from strawberry.fastapi import GraphQLRouter
            self.graphql_schema = strawberry_schema
            self.graphql_app = GraphQLRouter(self.graphql_schema, graphiql=True)
            self.include_router(self.graphql_app, prefix="/graphql", tags=["graphql"])

    async def get_healthcheck(self, request: Request) -> ORJSONResponse:
        """
        Get Healthcheck Status
        """
        msg: MSAHealthMessage = MSAHealthMessage()
        if not self.healthcheck:
            msg.message = "Healthcheck is disabled!"
        else:
            msg.healthy = self.healthcheck.is_healthy
            msg.message = await self.healthcheck.get_health()
            if len(self.healthcheck.error) > 0:
                msg.error = self.healthcheck.error

        return ORJSONResponse(content=jsonable_encoder(msg))

    async def get_services_status(self, request: Request) -> MSAServiceStatus:
        """
        Get Service Status Info
        """
        sst: MSAServiceStatus = MSAServiceStatus()
        if not self.healthcheck:
            sst.name = self.service_definition.name
            sst.healthy = "disabled:400"
            sst.message = "Healthcheck is disabled!"

        else:
            sst.name = self.service_definition.name
            sst.healthy = await self.healthcheck.get_health()
            sst.message = "Healthcheck is enabled!"

        return sst

    def get_services_definition(self, request: Request) -> MSAServiceDefinition:
        """
        Get Service Definition Info
        """
        return self.service_definition

    def get_services_settings(self, request: Request) -> ORJSONResponse:
        """
        Get Service OpenAPI Schema
        """

        def try_get_json():
            try:

                return jsonable_encoder(self.settings)

            except Exception as e:
                return {"status": "error:400", "error": e.__str__()}

        return ORJSONResponse(
            {
                self.service_definition.name: try_get_json(),
            }

        )

    def get_services_openapi_schema(self, request: Request) -> ORJSONResponse:
        """
        Get Service OpenAPI Schema
        """

        def try_get_json():
            try:

                return jsonable_encoder(self.openapi())

            except Exception as e:
                return {"status": "error:400", "error": e.__str__()}

        return ORJSONResponse(
            {
                self.service_definition.name: try_get_json(),
            }

        )

    def get_services_openapi_info(self, request: Request) -> MSAOpenAPIInfo:
        """
        Get Service OpenAPI Info
        """
        oai: MSAOpenAPIInfo = MSAOpenAPIInfo()

        try:
            oai.name = self.title
            oai.version = self.openapi_version
            oai.url = self.openapi_url
            oai.tags = self.openapi_tags
        except Exception as e:
            oai.tags = ["error:400 error" + e.__str__()]

        return oai

    def index_page(self, request: Request):
        """
        Get Service Index.html Page
        """
        return self.templates.TemplateResponse("index.html",
                                               {"request": request,
                                                "settings": jsonable_encoder(self.settings),
                                                "definitions": jsonable_encoder(self.service_definition)})

    def testpage(self, request: Request):
        """
        Simple Testpage to see if the Micro Service is up and running.
        Only works if pages is enabled in MSAServiceDefinition
        :param request:
        :return:
        """
        return self.templates.TemplateResponse("test.html",
                                               {"request": request,
                                                "settings": jsonable_encoder(self.settings)})

    async def monitor(self, request: Request):
        """
        Simple Service Monitor Page.
        Only works if pages is enabled in MSAServiceDefinition
        :param request:
        :return:
        """
        sysinfo: MSASystemInfo = await get_sysinfo()
        return self.templates.TemplateResponse("monitor.html",
                                               {"request": request,
                                                "outputSystemInfo": sysinfo})

    def profiler(self, request: Request):
        """
        Simple Profiler Page.
        Only works if pages is enabled in MSAServiceDefinition
        :param request:
        :return:
        """
        return self.templates.TemplateResponse("profiler.html",
                                               {"request": request})

    async def monitor_inline(self, request: Request):
        """
        Simple Monitor Page as Inline without head and body tags.
        Only works if pages is enabled in MSAServiceDefinition
        :param request:
        :return:
        """
        sysinfo: MSASystemInfo = await get_sysinfo()
        return self.templates.TemplateResponse("monitor_inline.html",
                                               {"request": request,
                                                "outputSystemInfo": sysinfo})
