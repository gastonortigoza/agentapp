"""OpenInference traces sent only to the local Phoenix collector."""
from functools import wraps
import json
import requests
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from openinference.instrumentation.crewai import CrewAIInstrumentor

session = requests.Session()
session.trust_env = False
provider = TracerProvider(resource=Resource.create({'service.name': 'local-ai-lab', 'openinference.project.name': 'Agentes locales'}))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint='http://127.0.0.1:6006/v1/traces', session=session, headers={}, timeout=2)))
tracer = provider.get_tracer('local-ai-lab')
CrewAIInstrumentor().instrument(tracer_provider=provider)

def traced(name, kind='CHAIN'):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with tracer.start_as_current_span(name, attributes={'openinference.span.kind': kind}) as span:
                if kind == 'LLM':
                    span.set_attribute('llm.model_name', str(args[0].model))
                    span.set_attribute('input.value', json.dumps(args[1] if len(args)>1 else kwargs.get('messages'), ensure_ascii=False, default=str))
                result = function(*args, **kwargs)
                failed = isinstance(result, dict) and result.get('status') in ('fail', 'failed', 'error')
                span.set_status(Status(StatusCode.ERROR if failed else StatusCode.OK))
                if kind == 'LLM':
                    span.set_attribute('output.value', str(result))
                return result
        return wrapped
    return decorate
