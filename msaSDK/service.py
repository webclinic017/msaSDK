# -*- coding: utf-8 -*-
"""Main Service Module for MSAApp.

Initialize with a MSAServiceDefintion Instance to control the features and functions of the MSAApp.

"""


import asyncio
import datetime
import os
from asyncio import Task
from datetime import timedelta
from typing import List, Optional, Any, Union

import uvloop
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from fastapi_pagination import add_pagination
from fastapi_users.password import PasswordHelper
from fastapi_utils.timing import add_timing_middleware
from loguru import logger as logger_gruru
from msgpack_asgi import MessagePackMiddleware
from passlib.context import CryptContext
from prometheus_fastapi_instrumentator import Instrumentator
from redbird.repos import MemoryRepo
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import DeclarativeMeta
from sqlmodel import SQLModel
from starception import StarceptionMiddleware
from starlette import status
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates, _TemplateResponse
from starlette_context import plugins
from starlette_context.middleware import RawContextMiddleware
from starlette_wtf import CSRFProtectMiddleware
from strawberry import schema
from tinydb import TinyDB
from tinydb.storages import MemoryStorage

from msaSDK.db.crud import MSASQLModelCrud
from msaSDK.models.health import MSAHealthMessage
from msaSDK.models.service import MSAServiceDefinition, MSAHealthDefinition
from msaSDK.msaapi import MSAFastAPI
from msaSDK.router.system import sys_router
from msaSDK.security import getMSASecurity
from msaSDK.utils import healthcheck as health
from msaSDK.utils.logger import init_logging
from msaSDK.utils.profiler import MSAProfilerMiddleware
from msaSDK.utils.scheduler import MSAScheduler
from msaSDK.utils.sysinfo import get_sysinfo, MSASystemInfo

security_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
"""Security Context for Password Helper"""
password_helper = PasswordHelper(security_context)
"""Password Helper Instance"""
security = getMSASecurity()
"""MSASecurity instance"""


class MSASchedulerRepoLogRecord(SQLModel):
    task_name: str
    action: str
    created: Optional[datetime.datetime]
    name: str
    msg: str
    levelname: Any
    levelno: int
    pathname: str
    filename: str
    module: str
    exc_text: Any
    lineno: int
    funcName: str
    msecs: float
    relativeCreated: Optional[datetime.datetime]
    thread: int
    threadName: str
    processName: str
    process: int
    message: Any
    formatted_message: Any


class MSASchedulerTaskDetail(SQLModel):
    permanent_task: bool
    fmt_log_message: str
    daemon: Any
    name: str
    description: Any
    logger_name: str
    execution: Any
    priority: int
    disabled: bool
    force_run: bool
    force_termination: bool
    status: str
    timeout: Optional[Union[str, int, timedelta]]
    parameters: Any
    start_cond: Any
    end_cond: Any
    on_startup: bool
    on_shutdown: bool
    last_run: Optional[datetime.datetime]
    last_success: Optional[datetime.datetime]
    last_fail: Optional[datetime.datetime]
    last_terminate: Optional[datetime.datetime]
    last_inaction: Optional[datetime.datetime]
    last_crash: Optional[datetime.datetime]
    func: Any
    path: Any
    func_name: str
    cache: bool
    sys_paths: List


class MSASchedulerTaskStatus(SQLModel):
    """**MSASchedulerTaskStatus** Pydantic Response Class
    """
    name: Optional[str] = None
    """Task Name."""
    detail: Optional[MSASchedulerTaskDetail] = None
    """Task detail."""


class MSASchedulerStatus(SQLModel):
    """
    **MSASchedulerStatus** Pydantic Response Class
    """
    name: Optional[str] = "msaSDK Service"
    """Service Name."""
    tasks: Optional[List[MSASchedulerTaskStatus]] = []
    """Optional MSASchedulerTaskStatus List"""
    message: Optional[str] = "None"
    """Optional Message Text"""


class MSASchedulerLog(SQLModel):
    """
    **MSASchedulerStatus** Pydantic Response Class
    """
    name: Optional[str] = "msaSDK Service"
    """Service Name."""
    log: Optional[List[MSASchedulerRepoLogRecord]] = []
    """Optional MSASchedulerRepoLogRecord List"""
    message: Optional[str] = "None"
    """Optional Message Text"""


class MSAServiceStatus(SQLModel):
    """
    **MSAServiceStatus** Pydantic Response Class
    """
    name: Optional[str] = "msaSDK Service"
    """Service Name."""
    healthy: Optional[str] = "None"
    """Health status"""
    message: Optional[str] = "None"
    """Optional Message Text"""


class MSAOpenAPIInfo(SQLModel):
    """
    **MSAOpenAPIInfo** Pydantic Response Class
    """
    name: str = "msaSDK Service"
    """Service Name."""
    version: str = "0.0.0"
    """API Version."""
    url: str = "/openapi.json"
    """OpenAPI URL."""
    tags: Optional[List[str]] = None
    """OpenAPI Tags."""


def getSecretKey():
    """
    Get Secret Key for Token creation from OS Environment Variable **SECRET_KEY_TOKEN**

    Returns:
        key: The SECRET_KEY_TOKEN.

    """
    key: str = os.getenv("SECRET_KEY_TOKEN",
                             "u2dmsaservicex_#M8A{1o3Bd?<ipwt^K},Z)OE<Fkj-X9IILWq|Cf`Y:HFI~&2L%Ion3}+p{T%")
    return key


def getSecretKeySessions():
    """
    Get Secret Key for Session Middleware from OS Environment Variable **SECRET_KEY_SESSIONS**

    Returns:
        key: The SECRET_KEY_SESSIONS.

    """
    key: str = os.getenv("SECRET_KEY_SESSIONS",
                             "u2dmsaserviceeP)zg5<g@4WJ0W8'?ad!T9UBvW1z2k|y~|Pgtewv=H?GY_Q]t~-~UUe'pJ0V[>!<)")
    return key


def getSecretKeyCSRF() -> str:
    """
    Get Secret Key for CSRF Middleware from OS Environment Variable **SECRET_KEY_CSRF**

    Returns:
        key: The SECRET_KEY_CSRF.

    """
    key: str = os.getenv("SECRET_KEY_CSRF",
                             "u2dmsaservicee_rJM'onkEV1trD=I7dci$flB)aSNW+raL4j]Ww=n~_BRg35*3~(E.>rx`1aTw:s")
    return key


class MSAApp(MSAFastAPI):
    """Creates an application msaSDK instance.

    Note:
        As with FastApi the MSAApp provides two events:
        ``startup``: A list of callables to run on application startup. Startup handler callables do not take any arguments, and may be be either standard functions, or async functions.
        ``shutdown``: A list of callables to run on application shutdown. Shutdown handler callables do not take any arguments, and may be be either standard functions, or async functions.
        Those are also used internally, which are triggered before the external events.

        Do not include the `self` parameter in the ``Args`` section.

    Args:
        settings: MSAServiceDefinition (Must be provided), instance of a service definition with all settings
        sql_models: List of SQLModel Default None, provide list of your SQLModel Classes and the instance can create CRUD API and if site is enabled also UI for CRUD
        auto_mount_site: Default True, if site is enabled in settings and this is true, mounts the site in internal startup event.

    Attributes:
        logger: loguru logger instance
        auto_mount_site: bool auto_mount_site
        settings: MSAServiceDefinition settings instance.
        healthdefinition: MSAHealthDefinition settings.healthdefinition
        limiter: Limiter = None
        db_engine: AsyncEngine = Db Engine instance
        sql_models: List[SQLModel] = sql_models
        sql_cruds: List[MSASQLModelCrud] = []
        scheduler: MSAScheduler = None
        site: AdminSite Admin/Auth Site instance.
        scheduler_task: The Task instance that runs the Scheduler in the Background
        ROOTPATH: str os.path.join(os.path.dirname(__file__))

    """

    def __init__(
            self,
            settings: MSAServiceDefinition,
            sql_models: List[SQLModel] = None,
            auto_mount_site: bool = True,
            *args,
            **kwargs
    ) -> None:
        # call super class __init__
        super().__init__(*args, **settings.fastapi_kwargs)
        self.logger = logger_gruru
        init_logging()
        self.auto_mount_site: bool = auto_mount_site
        self.settings = settings
        self.healthdefinition: MSAHealthDefinition = self.settings.healthdefinition
        self.limiter: Limiter = None
        self.sqlite_db_engine: AsyncEngine = None
        self.json_db_engine: TinyDB = None
        self.sql_models: List[SQLModel] = sql_models
        self.sql_cruds: List[MSASQLModelCrud] = []
        self.scheduler: MSAScheduler = None
        self.site = None
        self._scheduler_task: Task = None
        self.ROOTPATH = os.path.join(os.path.dirname(__file__))

        if self.settings.uvloop:
            self.logger.info("Enable UVLoop")
            uvloop.install()
        else:
            self.logger.info("Excluded UVLoop")

        self.healthcheck: health.MSAHealthCheck = None

        if self.settings.validationception:
            self.logger.info("Add Handler ValidationError")
            self.add_exception_handler(RequestValidationError, self.validation_exception_handler)
        else:
            self.logger.info("Excluded Handler ValidationError")

        if self.settings.httpception:
            self.logger.info("Add Handler HTTPException")
            self.add_exception_handler(StarletteHTTPException, self.msa_exception_handler)
        else:
            self.add_exception_handler(StarletteHTTPException, self.msa_exception_handler_disabled)
            self.logger.info("Excluded Handler HTTPException")

        if self.settings.starception:
            self.logger.info("Add Middleware Starception")
            self.add_middleware(StarceptionMiddleware)
        else:
            self.logger.info("Excluded Middleware Starception")

        if not self.settings.site:
            self.logger.info("Excluded Admin Site")
        if not self.settings.site_auth:
            self.logger.info("Excluded Admin Auth Site")

        if self.settings.json_db:
            self.logger.info("JSON DB - Init: " + self.settings.sqlite_db_url)
            if self.settings.json_db_memory_only:
                self.json_db_engine = TinyDB(self.settings.json_db_url, storage=MemoryStorage)
            else:
                self.json_db_engine = TinyDB(self.settings.json_db_url, storage=TinyDB.default_storage_class)
        else:
            self.logger.info("JSON Excluded DB")

        if self.settings.sqlite_db or (self.settings.scheduler and self.settings.scheduler_log_to_db):
            self.logger.info("SQLite DB - Init: " + self.settings.sqlite_db_url)
            self.Base: DeclarativeMeta = declarative_base()
            self.sqlite_db_engine = create_async_engine(self.settings.sqlite_db_url, echo=self.settings.sqlite_db_debug, future=True)
            if (self.settings.sqlite_db_crud or self.settings.site) and self.sql_models:
                self.logger.info("SQLite DB - Register/CRUD SQL Models: " + str(self.sql_models))
                # register all Models and the crud for them
                for model in self.sql_models:
                    new_crud: MSASQLModelCrud = MSASQLModelCrud(model=model, engine=self.sqlite_db_engine).register_crud()
                    if self.settings.sqlite_db_crud:
                        self.include_router(new_crud.router)
                    self.sql_cruds.append(new_crud)
        else:
            self.logger.info("Excluded SQLiteDB")

        if self.settings.graphql:
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
                host=self.settings.host,
                port=self.settings.port
            )
            self.logger.info("Start Healthcheck Thread")
            self.healthcheck.start()
            self.add_api_route(self.healthdefinition.path, self.get_healthcheck,
                               response_model=MSAHealthMessage,
                               tags=["service"])
        else:
            self.logger.info("Excluded Healthcheck")

        if self.settings.sysrouter:
            self.logger.info("Include Sysrouter")
            self.include_router(sys_router)
        else:
            self.logger.info("Excluded Sysrouter")

        if self.settings.cors:
            self.logger.info("Add Middleware CORS")
            self.add_middleware(CORSMiddleware, allow_origins=self.settings.allow_origins,
                                allow_credentials=self.settings.allow_credentials,
                                allow_methods=self.settings.allow_methods,
                                allow_headers=self.settings.allow_headers, )
        else:
            self.logger.info("Excluded Middleware CORS")

        if self.settings.httpsredirect:
            self.logger.info("Add Middleware HTTPSRedirect")
            self.add_middleware(HTTPSRedirectMiddleware)
        else:
            self.logger.info("Excluded Middleware HTTPSRedirect")

        if self.settings.gzip:
            self.logger.info("Add Middleware GZip")
            self.add_middleware(GZipMiddleware)
        else:
            self.logger.info("Excluded Middleware GZip")

        if self.settings.session:
            self.logger.info("Add Middleware Session")
            self.add_middleware(SessionMiddleware, secret_key=getSecretKeySessions())
        else:
            self.logger.info("Excluded Middleware Session")

        if self.settings.csrf:
            self.logger.info("Add Middleware CSRF")
            self.add_middleware(CSRFProtectMiddleware, csrf_secret=getSecretKeyCSRF())
        else:
            self.logger.info("Excluded Middleware CSRF")

        if self.settings.msgpack:
            self.logger.info("Add Middleware MSGPack")
            self.add_middleware(MessagePackMiddleware)
        else:
            self.logger.info("Excluded Middleware MSGPack")

        if self.settings.context:
            self.logger.info("Add Middleware Context")
            self.add_middleware(RawContextMiddleware, plugins=(
                plugins.RequestIdPlugin(),
                plugins.CorrelationIdPlugin()
            ))
        else:
            self.logger.info("Excluded Middleware Context")

        if self.settings.profiler:
            self.logger.info("Add Middleware Profiler")
            self.add_middleware(MSAProfilerMiddleware,
                                profiler_output_type=self.settings.profiler_output_type,
                                track_each_request=self.settings.profiler_single_calls,
                                msa_app=self)
        else:
            self.logger.info("Excluded Middleware Profiler")

        if self.settings.timing:
            self.logger.info("Add Middleware Timing")
            add_timing_middleware(self, record=self.logger.info, prefix="app", exclude="untimed")
        else:
            self.logger.info("Excluded Middleware Timing")

        if self.settings.limiter:
            self.logger.info("Add Limiter Engine")
            self.limiter = Limiter(key_func=get_remote_address)
            self.state.limiter = self.limiter
            self.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        else:
            self.logger.info("Excluded Limiter Engine")

        if self.settings.servicerouter:
            self.logger.info("Include Servicerouter")
            self.add_api_route("/scheduler", self.get_scheduler_status, tags=["service"],
                               response_model=MSASchedulerStatus)
            self.add_api_route("/scheduler_log", self.get_scheduler_log, tags=["service"],
                               response_model=MSASchedulerLog)
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

        if self.settings.static or self.settings.pages:
            self.logger.info("Mount MSAStatic")
            self.mount("/msastatic", StaticFiles(directory="msastatic"), name="msastatic")
        else:
            self.logger.info("Excluded MSAStatic")

        if self.settings.pagination:
            self.logger.info("Add Pagination Engine")
            add_pagination(self)
        else:
            self.logger.info("Excluded Pagination Engine")

        if self.settings.templates or self.settings.pages:
            self.logger.info("Init Jinja MSAUITemplate Engine")
            self.templates = Jinja2Templates(directory=self.settings.templates_dir)
        else:
            self.logger.info("Excluded Jinja MSAUITemplate Engine")

        if self.settings.pages:
            self.logger.info("Add Pages Router")
            self.add_api_route(self.settings.profiler_url, self.profiler, tags=["pages"], response_class=HTMLResponse)
            self.add_api_route("/testpage", self.testpage, tags=["pages"], response_class=HTMLResponse)
            if not self.settings.site:
                self.add_api_route("/", self.index_page, tags=["pages"], response_class=HTMLResponse)
                self.add_api_route("/monitor", self.monitor, tags=["pages"], response_class=HTMLResponse)
                self.add_api_route("/monitor_inline", self.monitor_inline, tags=["pages"], response_class=HTMLResponse)
        else:
            self.logger.info("Excluded Pages Router")

        if self.settings.instrument:
            self.logger.info("Prometheus Instrument and Expose App")
            Instrumentator().instrument(app=self).expose(app=self, include_in_schema=True, tags=["service"],
                                                         response_class=HTMLResponse)
        else:
            self.logger.info("Excluded Prometheus Instrument and Expose")

        self.logger.info("Events - Add Internal Handlers")
        self.add_event_handler("shutdown", self.shutdown_event)
        self.add_event_handler("startup", self.startup_event)

        if self.settings.scheduler:
            self.logger.info("Add Scheduler")
            self.scheduler = MSAScheduler(config={"task_execution": "async"})

        elif not self.settings.scheduler:
            self.logger.info("Excluded Scheduler, Disabled")

    async def startup_event(self) -> None:
        """

        :return:
        :rtype:
        """
        self.logger.info("msaSDK Internal Startup MSAUIEvent")

        if self.settings.sqlite_db:
            async with self.sqlite_db_engine.begin() as conn:
                if self.settings.sqlite_db_meta_drop:
                    self.logger.info("SQLite DB - Drop Meta All: " + self.settings.sqlite_db_url)
                    await conn.run_sync(SQLModel.metadata.drop_all)
                if self.settings.sqlite_db_meta_create:
                    self.logger.info("SQLite DB - Create Meta All: " + self.settings.sqlite_db_url)
                    await conn.run_sync(SQLModel.metadata.create_all)
            await self.sqlite_db_engine.dispose()

        if self.settings.site or self.settings.site_auth:

            site = None
            if self.settings.site_auth:
                self.logger.info("Add Admin Site with Auth")
                from msaSDK.auth.site import AuthAdminSite
                site = AuthAdminSite(msa_app=self)
                try:
                    await site.db.async_run_sync(SQLModel.metadata.create_all, is_session=False)
                    await site.auth.create_role_user('admin')
                except Exception as e:
                    pass
            else:
                self.logger.info("Add Admin Site without Auth")
                from msaSDK.admin import AdminSite
                site = AdminSite(msa_app=self)

            self.site = site
            if site and self.auto_mount_site:
                self.mount_site()

        if self.settings.scheduler:
            self.logger.info("Scheduler - Start")
            self._scheduler_task = asyncio.create_task(self.scheduler.serve(debug=self.debug), name="MSA_Scheduler")

    def mount_site(self) -> None:
        if self.site:
            self.logger.info("Mount Admin Site")
            self.site.mount_app(self)
        else:
            self.logger.error("Can't Mount Admin Site - Not initialized or enabled")

    async def shutdown_event(self) -> None:
        self.logger.info("msaSDK Internal Shutdown MSAUIEvent")
        if self.settings.scheduler:
            self.logger.info("Stop Schedulers")

            self.logger.info("Cancel Scheduler")
            if not self._scheduler_task.cancelled():
                try:
                    self._scheduler_task.cancel()
                except Exception as ex:
                    self.logger.error(f"_scheduler_task cancel failed")
                    pass

            self.logger.info("End Scheduler")
            self._scheduler_task = None
            del self._scheduler_task

        if self.site:
            self.logger.info("Stopping Site")
            self.site = None

        if self.healthcheck:
            self.logger.info("Stopping Healthcheck Thread")
            await self.healthcheck.stop()
            self.healthcheck = None

        if self.settings.json_db:
            self.logger.info("JSON DB - Close: " + self.settings.sqlite_db_url)
            self.json_db_engine.close()

        if self.settings.sqlite_db:
            self.logger.info("SQLite DB - Dispose Connections: " + self.settings.sqlite_db_url)
            await self.sqlite_db_engine.dispose()

    async def _init_graphql(self, strawberry_schema: schema) -> None:
        """
        Internal helper function to initialize the graphql router
        """
        if self.settings.graphql:
            from strawberry.fastapi import GraphQLRouter
            self.graphql_schema = strawberry_schema
            self.graphql_app = GraphQLRouter(self.graphql_schema, graphiql=True)
            self.include_router(self.graphql_app, prefix="/graphql", tags=["graphql"])

    async def get_healthcheck(self, request: Request) -> ORJSONResponse:
        """
        Get Healthcheck Status
        """
        self.logger.info("Called - get_healthcheck :" + str(request.url))
        msg: MSAHealthMessage = MSAHealthMessage()
        if not self.healthcheck:
            msg.message = "Healthcheck is disabled!"
        else:
            msg.healthy = self.healthcheck.is_healthy
            msg.message = await self.healthcheck.get_health()
            if len(self.healthcheck.error) > 0:
                msg.error = self.healthcheck.error

        return ORJSONResponse(content=jsonable_encoder(msg))

    async def get_scheduler_status(self, request: Request) -> MSASchedulerStatus:
        """
        Get Service Scheduler Status, with the registered Task's

        Args:
            request: The input http request object

        Returns:
            sst: MSASchedulerStatus Pydantic Response Model

        """
        self.logger.info("Called - get_scheduler_status :" + str(request.url))
        sst: MSASchedulerStatus = MSASchedulerStatus()
        if not self.settings.scheduler:
            sst.name = self.settings.name
            sst.message = "Scheduler is disabled!"

        else:
            sst.name = self.settings.name
            for task in self.scheduler.session.tasks:
                nt: MSASchedulerTaskStatus = MSASchedulerTaskStatus()
                nt.name = task.name
                nt.detail = MSASchedulerTaskDetail.parse_obj(task)
                sst.tasks.append(nt)
            sst.message = "Scheduler is enabled!"

        return sst

    async def get_scheduler_log(self, request: Request, optionClearLog: bool = False,
                                optionFORCEClearLog: bool = False) -> MSASchedulerLog:
        """
        Get Service Scheduler Log

        Args:
            request: The input http request object
            optionClearLog: If True the Log gets cleared after the response was build
            optionFORCEClearLog: Forcing the clearing of the log before the response gets created

        Returns:
            sst: MSASchedulerLog Pydantic Response Model

        """
        self.logger.info("Called - get_scheduler_log :" + str(request.url))
        sst: MSASchedulerLog = MSASchedulerLog()
        if not self.settings.scheduler:
            sst.name = self.settings.name
            sst.message = "Scheduler is disabled!"

        else:
            sst.name = self.settings.name
            repo: MemoryRepo = self.scheduler.session.get_repo()
            if optionFORCEClearLog:
                repo.collection.clear()
            for log_entry in repo.filter_by().all():
                le: MSASchedulerRepoLogRecord = MSASchedulerRepoLogRecord.parse_obj(log_entry)
                sst.log.append(le)
            if optionClearLog:
                repo.collection.clear()
                sst.message = "Scheduler is enabled! Scheduler Log cleared!"
            else:
                sst.message = "Scheduler is enabled!"

        return sst

    async def get_services_status(self, request: Request) -> MSAServiceStatus:
        """
        Get Service Status Info

        Args:
            request: The input http request object

        Returns:
            sst: MSAServiceStatus Pydantic Response Model

        """
        self.logger.info("Called - get_services_status :" + str(request.url))
        sst: MSAServiceStatus = MSAServiceStatus()
        if not self.healthcheck:
            sst.name = self.settings.name
            sst.healthy = "disabled:400"
            sst.message = "Healthcheck is disabled!"

        else:
            sst.name = self.settings.name
            sst.healthy = await self.healthcheck.get_health()
            sst.message = "Healthcheck is enabled!"

        return sst

    def get_services_definition(self, request: Request) -> MSAServiceDefinition:
        """
        Get Service Definition Info

        Args:
            request: The input http request object

        Returns:
            settings: MSAServiceDefinition Pydantic Response Model

        """
        self.logger.info("Called - get_services_definition :" + str(request.url))
        return self.settings

    def get_services_settings(self, request: Request) -> ORJSONResponse:
        """
        Get Service OpenAPI Schema

        Args:
            request: The input http request object

        Returns:
            settings: ORJSONResponse

        """
        self.logger.info("Called - get_services_settings :" + str(request.url))

        def try_get_json():
            try:

                return jsonable_encoder(self.settings)

            except Exception as e:
                return {"status": "error:400", "error": e.__str__()}

        return ORJSONResponse(
            {
                self.settings.name: try_get_json(),
            }

        )

    def get_services_openapi_schema(self, request: Request) -> ORJSONResponse:
        """
        Get Service OpenAPI Schema

        Args:
            request: The input http request object

        Returns:
            openapi: ORJSONResponse openapi schema


        """
        self.logger.info("Called - get_services_openapi_schema :" + str(request.url))

        def try_get_json():
            try:

                return jsonable_encoder(self.openapi())

            except Exception as e:
                return {"status": "error:400", "error": e.__str__()}

        return ORJSONResponse(
            {
                self.settings.name: try_get_json(),
            }

        )

    def get_services_openapi_info(self, request: Request) -> MSAOpenAPIInfo:
        """
        Get Service OpenAPI Info

        Args:
            request: The input http request object

        Returns:
            oai: MSAOpenAPIInfo Paydantic Response Model

        """
        self.logger.info("Called - get_services_openapi_info :" + str(request.url))
        oai: MSAOpenAPIInfo = MSAOpenAPIInfo()

        try:
            oai.name = self.title
            oai.version = self.openapi_version
            oai.url = self.openapi_url
            oai.tags = self.openapi_tags
        except Exception as e:
            oai.tags = ["error:400 error" + e.__str__()]

        return oai

    async def validation_exception_handler(self, request: Request, exc: RequestValidationError) -> JSONResponse:
        self.logger.error("validation_exception_handler - " + str(exc.errors()))
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=jsonable_encoder({"detail": exc.errors(), "body": exc.body}),
        )

    async def msa_exception_handler_disabled(self, request: Request, exc: HTTPException) -> JSONResponse:
        """
        Handles all HTTPExceptions if Disabled with JSON Response.

        Args:
            request: The input http request object

        Returns:
            HTTPException: as JSONResponse

        """

        error_content = jsonable_encoder({"status_code": exc.status_code,
                                          "detail": exc.detail,
                                          "args": exc.args,
                                          "headers": exc.headers,
                                          "request": request.url,
                                          })
        self.logger.error("msa_exception_handler_disabled - " + str(error_content))
        return JSONResponse(
            status_code=exc.status_code,
            content=error_content,
        )

    async def msa_exception_handler(self, request: Request, exc: HTTPException):
        """
        Handles all HTTPExceptions if enabled with HTML Response or forward error if the code is in the exclude settings list.

        Args:
            request: The input http request object
            exc : The HTTPException instance

        Returns:
            HTTPException or Template

        """
        error_content = {'request': request, 'detail': exc.detail,
                         'status': exc.status_code,
                         "definitions": jsonable_encoder(self.settings)}
        self.logger.error("msa_exception_handler - " + str(error_content))
        if exc.status_code == 403:
            return self.templates.TemplateResponse('403.html', error_content)
        elif exc.status_code == 404:
            return self.templates.TemplateResponse('404.html', error_content)
        elif exc.status_code == 500:
            return self.templates.TemplateResponse('500.html', error_content)
        elif exc.status_code in self.settings.httpception_exclude:
            return await http_exception_handler(request, exc)
        else:
            # Generic error page
            return self.templates.TemplateResponse('error.html', error_content)

    def index_page(self, request: Request) -> _TemplateResponse:
        """
        Get Service Index.html Page

        Args:
            request: The input http request object

        """
        self.logger.info("Called - index_page :" + str(request.url))
        return self.templates.TemplateResponse("index.html",
                                               {"request": request,
                                                "settings": jsonable_encoder(self.settings),
                                                "definitions": jsonable_encoder(self.settings)})

    def testpage(self, request: Request) -> _TemplateResponse:
        """
        Simple Testpage to see if the Micro Service is up and running.
        Only works if pages is enabled in MSAServiceDefinition

        Args:
            request: The input http request object
        """
        self.logger.info("Called - testpage :" + str(request.url))
        return self.templates.TemplateResponse("test.html",
                                               {"request": request,
                                                "settings": jsonable_encoder(self.settings)})

    async def monitor(self, request: Request) -> _TemplateResponse:
        """
        Simple Service Monitor Page.
        Only works if pages is enabled in MSAServiceDefinition

        Args:
            request: The input http request object
        """
        self.logger.info("Called - monitor :" + str(request.url))
        sysinfo: MSASystemInfo = get_sysinfo()
        return self.templates.TemplateResponse("monitor.html",
                                               {"request": request,
                                                "outputSystemInfo": sysinfo})

    def profiler(self, request: Request) -> _TemplateResponse:
        """
        Simple Profiler Page.
        Only works if pages is enabled in MSAServiceDefinition

        Args:
            request: The input http request object
        """
        self.logger.info("Called - profiler :" + str(request.url))
        return self.templates.TemplateResponse("profiler.html",
                                               {"request": request})

    async def monitor_inline(self, request: Request) -> _TemplateResponse:
        """
        Simple Monitor Page as Inline without head and body tags.
        Only works if pages is enabled in MSAServiceDefinition

        Args:
            request: The input http request object
        """
        self.logger.info("Called - monitor_inline :" + str(request.url))
        sysinfo: MSASystemInfo = get_sysinfo()
        return self.templates.TemplateResponse("monitor_inline.html",
                                               {"request": request,
                                                "outputSystemInfo": sysinfo})
