"""Verified ARE typed-result clones; source and field matching is exact."""
from copy import copy
from dataclasses import replace
from .contracts import AttackApplyResult


class TypedTextSurface:
    modes=('single_entry_tool_result_text',)
    phase='tool_result'
    fields={'Emails__list_emails':('email_id','emails[].content'),
        'Emails__get_email_by_id':('email_id','content'),
        'Chats__read_conversation':('message_id','messages[].content'),
        'Messages__read_conversation':('message_id','messages[].content'),
        'Chats__list_recent_conversations':('message_id','[].messages[].content'),
        'Messages__list_recent_conversations':('message_id','[].messages[].content')}

    def source_tool(self,spec):
        return spec.source_tool

    def validate(self,spec):
        if spec.source_tool not in self.fields or spec.receiver_policy != 'first_actual_reader':
            raise ValueError('unsupported_new_attack_surface_or_receiver')
        prefix,field=self.fields[spec.source_tool]
        if not spec.source_selector.startswith(prefix+':') or not spec.source_selector[len(prefix)+1:]:
            raise ValueError('unsupported_exact_source_selector')
        if spec.field_path != field:
            raise ValueError('unsupported_new_attack_field_or_evaluator')

    def receiver_allowed(self,context,receiver_agent_id,receiver_prefixes):
        return context.caller_id=='agent_0' or context.caller_id.startswith('worker_')

    def apply(self,spec,context,payload):
        from are.simulation.apps.email_client import Email,ReturnedEmails
        from are.simulation.apps.messaging_v2 import MessageV2,ConversationV2
        result=context.tool_result
        tool_name=context.tool_name
        selected=spec.source_selector.split(':',1)[1]
        matches=[]
        def locate(items,kind,id_field,conversation=None):
            for i,item in enumerate(items):
                if type(item) is not kind:
                    raise TypeError('BLOCKED_RETURN_TYPE')
                if getattr(item,id_field)==selected and (spec.source_conversation_id is None or conversation==spec.source_conversation_id):
                    matches.append((items,i,item))
        if tool_name=='Emails__list_emails':
            if type(result) is not ReturnedEmails:
                raise TypeError('BLOCKED_RETURN_TYPE')
            items=list(result.emails);locate(items,Email,'email_id')
            build=lambda:replace(result,emails=items)
        elif tool_name=='Emails__get_email_by_id':
            if type(result) is not Email:
                raise TypeError('BLOCKED_RETURN_TYPE')
            items=[result];locate(items,Email,'email_id');build=lambda:items[0]
        elif tool_name.endswith('read_conversation'):
            if type(result) is not dict or type(result.get('messages')) is not list:
                raise TypeError('BLOCKED_RETURN_TYPE')
            if spec.source_conversation_id is not None and result.get('metadata',{}).get('conversation_id')!=spec.source_conversation_id:
                return AttackApplyResult(False,False,result,'conversation_id_mismatch')
            items=list(result['messages'])
            for i,item in enumerate(items):
                if type(item) is not MessageV2:
                    raise TypeError('BLOCKED_RETURN_TYPE')
                if item.message_id==selected:
                    matches.append((items,i,item))
            build=lambda:{**result,'messages':items}
        else:
            if type(result) is not list:
                raise TypeError('BLOCKED_RETURN_TYPE')
            conversations=list(result)
            for position,conversation in enumerate(result):
                if type(conversation) is not ConversationV2:
                    raise TypeError('BLOCKED_RETURN_TYPE')
                items=list(conversation.messages);previous=len(matches)
                locate(items,MessageV2,'message_id',conversation.conversation_id)
                if len(matches)>previous:
                    clone=copy(conversation);clone.messages=items;conversations[position]=clone
            build=lambda:conversations
        if not matches:
            return AttackApplyResult(False,False,result,'exact_source_not_in_actual_return')
        if len(matches)!=1:
            raise ValueError('ambiguous_attack_source_id')
        items,index,item=matches[0]
        if not isinstance(item.content,str):
            raise TypeError('BLOCKED_RETURN_TYPE')
        modified=item.content+'\n'+payload
        items[index]=replace(item,content=modified)
        return AttackApplyResult(True,True,build(),'exact_text_field_modified',original_text=item.content,injected_text=modified)

    def read_probe(self,scenario,spec):
        """Free inspection of currently available objects, never future events."""
        app_name,method=self.source_tool(spec).split('__',1)
        app=scenario.get_app(app_name)
        if method=='list_emails':
            offset=0
            while True:
                value=app.list_emails(folder_name='INBOX',offset=offset,limit=50)
                yield value,{'page_offset':offset}
                if not value.emails or offset+len(value.emails)>=value.total_emails:
                    break
                offset+=len(value.emails)
        elif method=='get_email_by_id':
            yield app.get_email_by_id(spec.source_selector.split(':',1)[1]),{}
        elif method=='read_conversation':
            yield app.read_conversation(spec.source_conversation_id,limit=10),{}
        else:
            yield app.list_recent_conversations(limit=5),{}


class LegacyEmailSurface(TypedTextSurface):
    modes=('single_injection',)

    def source_tool(self,spec):
        return 'Emails__list_emails'

    def validate(self,spec):
        if not spec.source_selector.startswith('email_id:') or not spec.source_selector[9:]:
            raise ValueError('unsupported_email_source_selector')
        if spec.receiver_policy not in (None,'worker_only'):
            raise ValueError('unsupported_legacy_receiver')

    def receiver_allowed(self,context,receiver_agent_id,receiver_prefixes):
        return context.caller_id==receiver_agent_id if receiver_agent_id is not None else context.caller_id.startswith(receiver_prefixes)

    def apply(self,spec,context,payload):
        from are.simulation.apps.email_client import Email,ReturnedEmails
        result=context.tool_result
        if type(result) is not ReturnedEmails:
            raise TypeError('email_overlay_requires_ReturnedEmails')
        selected=spec.source_selector.removeprefix('email_id:')
        emails=list(result.emails)
        for index,email in enumerate(emails):
            if email.email_id!=selected:
                continue
            if type(email) is not Email or not isinstance(email.content,str):
                raise TypeError('email_overlay_requires_Email_content_str')
            modified=email.content+'\n'+payload
            emails[index]=replace(email,content=modified)
            return AttackApplyResult(True,True,replace(result,emails=emails),'exact_text_field_modified',
                                     original_text=email.content,injected_text=modified)
        return AttackApplyResult(False,False,result,'exact_source_not_in_actual_return')


class LegacyNotificationSurface:
    """Compatibility for the already existing legacy ARE Bridge only."""
    modes=('single_injection',)
    phase='environment_notification'

    def source_tool(self,spec):
        return 'are:environment_notification'

    def validate(self,spec):
        if spec.source_selector!='first_environment_notification':
            raise ValueError('UNSUPPORTED_environment_selector')
        if spec.receiver_policy is not None:
            raise ValueError('unsupported_legacy_receiver')

    def receiver_allowed(self,context,receiver_agent_id,receiver_prefixes):
        return True

    def apply(self,spec,context,payload):
        if not isinstance(context.tool_result,str):
            raise TypeError('notification_requires_text')
        modified=context.tool_result+'\n'+payload
        return AttackApplyResult(True,True,modified,'legacy_delivered_notification_modified',
            original_text=context.tool_result,injected_text=modified)
