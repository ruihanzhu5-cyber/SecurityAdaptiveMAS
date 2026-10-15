"""Small deterministic, source-anchored projections; never an Oracle/fact verifier."""
from dataclasses import replace,asdict
from datetime import date,datetime,timezone
import json
import math
import re
from .contracts import EvidenceField,EvidenceView


MONTHS={name.lower():i for i,name in enumerate(('January','February','March','April','May','June',
    'July','August','September','October','November','December'),1)}
MONTH_PATTERN=re.compile(r'\b('+'|'.join(MONTHS)+r')\s+(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?(?:,?\s+(20\d{2}))?\b',re.I)
ISO_PATTERN=re.compile(r'\b(20\d{2}-\d{2}-\d{2})(?:\s*(?:to|through|[-–])\s*(20\d{2}-\d{2}-\d{2}))?\b')
IDENTIFIER=re.compile(r'^[0-9a-fA-F-]{8,64}$')


def date_fields(text,ref,path,call_id,year):
    fields=[]
    for pattern in (MONTH_PATTERN,ISO_PATTERN):
        for match in pattern.finditer(text):
            try:
                if pattern is ISO_PATTERN:
                    start=date.fromisoformat(match[1]);end=date.fromisoformat(match[2] or match[1]);basis=None
                else:
                    selected_year=int(match[4]) if match[4] else year
                    if selected_year is None:
                        continue
                    start=date(selected_year,MONTHS[match[1].lower()],int(match[2]))
                    end=date(selected_year,start.month,int(match[3] or match[2]))
                    basis=None if match[4] else f'observed_simulation_year:{year}'
            except ValueError:
                # Invalid calendar literals are not normalized into an answer.
                continue
            if end<start:
                continue
            for name,value in (('date_start',start.isoformat()),('date_end',end.isoformat())):
                fields.append(EvidenceField(name,value,ref,path,match.span(),'deterministic_parse','anchored',call_id,basis))
    return fields


class EvidenceViewBuilder:
    VIEW='task_evidence_v1'
    MAX_FIELDS=512

    def __init__(self,session,required_fields):
        self.s=session
        self.required_fields=tuple(required_fields)
        self.ledger={}

    def source_records(self,ref):
        s=self.s
        pending=[ref];seen=set();result=[]
        while pending:
            item=pending.pop()
            if item.key in seen:
                continue
            seen.add(item.key)
            record=s.artifact_records[item.key]
            if record.ref!=item or item.key not in s.runtime.objects:
                raise ValueError('stale_or_unknown_evidence_source')
            if item.key in s.typed_snapshots:
                result.append(record)
            pending.extend(record.parent_refs)
        return result

    def typed_fields(self,record):
        from are.simulation.apps.email_client import Email,ReturnedEmails
        from are.simulation.apps.messaging_v2 import MessageV2,ConversationV2
        value=self.s.typed_snapshots[record.ref.key]
        fields=[]
        year=datetime.fromtimestamp(self.s.time_manager.time(),timezone.utc).year if self.s.time_manager is not None else None
        def scalar(name,value,path):
            valid=(type(value) in (int,float) and math.isfinite(value)) if name=='timestamp' else (
                isinstance(value,str) and IDENTIFIER.fullmatch(value) is not None)
            if valid:
                fields.append(EvidenceField(name,value,record.ref,path,None,'typed','validated',record.source_call_id))
        def email(item,path):
            scalar('email_id',item.email_id,path+'.email_id')
            scalar('timestamp',item.timestamp,path+'.timestamp')
            fields.extend(date_fields(item.content,record.ref,path+'.content',record.source_call_id,year))
        def message(item,path,cid,cid_path):
            scalar('conversation_id',cid,cid_path)
            for name in ('message_id','sender_id','timestamp'):
                scalar(name,getattr(item,name),path+'.'+name)
            fields.extend(date_fields(item.content,record.ref,path+'.content',record.source_call_id,year))
        if type(value) is ReturnedEmails:
            for i,item in enumerate(value.emails):
                if type(item) is not Email:
                    raise TypeError('unsupported_evidence_email_type')
                email(item,f'emails[{i}]')
        elif type(value) is Email:
            email(value,'email')
        elif type(value) is dict and 'messages' in value:
            cid=value['metadata']['conversation_id']
            for i,item in enumerate(value['messages']):
                if type(item) is not MessageV2:
                    raise TypeError('unsupported_evidence_message_type')
                message(item,f'messages[{i}]',cid,'metadata.conversation_id')
        elif type(value) is list and all(type(c) is ConversationV2 for c in value):
            for i,conversation in enumerate(value):
                for j,item in enumerate(conversation.messages):
                    if type(item) is not MessageV2:
                        raise TypeError('unsupported_evidence_message_type')
                    message(item,f'[{i}].messages[{j}]',conversation.conversation_id,f'[{i}].conversation_id')
        return fields

    def build(self,ref):
        fields=[field for record in self.source_records(ref) for field in self.typed_fields(record)]
        unique={json.dumps(asdict(f),sort_keys=True):f for f in fields}
        fields=list(unique.values())
        for name in ('date_start','date_end'):
            if len({f.value for f in fields if f.name==name})>1:
                fields=[replace(f,status='conflict') if f.name==name else f for f in fields]
        capacity_exceeded=len(fields)>self.MAX_FIELDS
        provided={f.name for f in fields if f.status in {'anchored','validated'}} if not capacity_exceeded else set()
        covered=tuple(name for name in self.required_fields if name in provided)
        missing=tuple(name for name in self.required_fields if name not in provided)
        # No body, quote, free-form Worker result or raw span is rendered.
        rendered={'schema':self.VIEW,'fields':[{'name':f.name,'value':f.value,'status':f.status,
            'source_ref':f.source_ref.key,'source_path':f.source_path} for f in fields
            if f.name in self.required_fields and f.status in {'anchored','validated'}] if not capacity_exceeded else [],
            'covered_required_fields':covered,'missing_required_fields':missing,
            'capacity_exceeded':capacity_exceeded,'fact_validation':'source_anchor_only_no_external_verification'}
        return EvidenceView(ref,tuple(fields),covered,missing,False,json.dumps(rendered,ensure_ascii=False))

    def ensure(self,ref):
        s=self.s
        if (ref.key,self.VIEW) in s.views:
            return s.views[(ref.key,self.VIEW)]
        view=self.build(ref)
        parents=tuple(dict.fromkeys([ref,*[f.source_ref for f in view.fields]]))
        record=s.artifact_records[ref.key]
        evidence=s.provenance.add(self.VIEW,'derived',view.rendered_content,
            parents=tuple(dict.fromkeys(r.object_id for r in parents)),owner_agent=record.producer_id)
        projected=s.register_artifact(evidence,record.producer_id,record.producer_work_id,payload_kind=self.VIEW,
            parents=parents,fields=view.covered_required_fields,view_id=self.VIEW,
            dependencies=tuple(dict.fromkeys(f.source_path for f in view.fields)),lineage=tuple(r.key for r in parents))
        s.views[(ref.key,self.VIEW)]=projected
        self.ledger[projected.key]=view
        s.trace.log('evidence_view_created',parent_ref=asdict(ref),view_ref=asdict(projected),
            covered_required_fields=view.covered_required_fields,missing_required_fields=view.missing_required_fields,
            fields=[asdict(f) for f in view.fields],contains_raw_untrusted_body=False,
            contains_untrusted_lineage=s.provenance.contains_low_trust(projected.object_id),
            rendered_bytes=len(view.rendered_content.encode()))
        return projected
