"""0.55.6: original planner gets one bounded self-audit before independent verification.

Offline AST harness uses the actual source methods without PostgreSQL/Redis/Ollama.
"""
from __future__ import annotations

import ast
import json
import logging
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
from norm_runtime.models import TaskPlan, TaskStep
from norm_runtime.protocol import PROTOCOL_VERSION

METHODS = {
    '_planner_schema', '_plan_verifier_schema', '_plan_self_review_schema',
    '_self_review_plan', '_tighten_plan', '_normalize_plan', '_generate_plan',
    '_check_plan', '_repair_plan', '_build_verified_plan',
}

def harness_type():
    source = (ROOT / 'core/norm_runtime/conversation_service.py').read_text('utf-8')
    tree = ast.parse(source)
    parent = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ConversationService')
    methods = [n for n in parent.body if isinstance(n, ast.FunctionDef) and n.name in METHODS]
    assert len(methods) == len(METHODS), {n.name for n in methods}
    harness = ast.ClassDef(name='PlanHarness', bases=[], keywords=[], body=methods, decorator_list=[])
    block = ast.fix_missing_locations(ast.Module(body=[harness], type_ignores=[]))
    ns = {'json':json, 'logging': logging, 'uuid': uuid, 'TaskPlan':TaskPlan, 'TaskStep':TaskStep, 'PROTOCOL_VERSION':PROTOCOL_VERSION}
    exec(compile(block, '<real-plan-methods>', 'exec'), ns)
    cls=ns['PlanHarness']
    cls._persistent_instruction_text=lambda self: ''
    return cls

Harness=harness_type()
DRAFT={'title':'Patch a project', 'steps':[
    {'name':'Inspect', 'description':'Inspect original source without modification', 'verify':'Record file locations'},
    {'name':'Patch', 'description':'Make the approved fix', 'verify':'Run exact targeted test'},
    {'name':'Deliver', 'description':'Summarize changes and provide result', 'verify':'User gets verified artifact'},
]}
REFINED={'title':'Patch a project', 'steps':[
    {'name':'Inspect', 'description':'Inspect original source without modification', 'verify':'Record file locations'},
    {'name':'Patch', 'description':'Make the approved fix and avoid duplicate file reads', 'verify':'Run exact targeted test'},
    {'name':'Deliver', 'description':'Summarize changes and provide result', 'verify':'User gets verified artifact'},
]}
SIMPLE={'title':'Respond', 'steps':[
    {'name':'Reply', 'description':'Greet user succinctly', 'verify':'Answer directly'}
]}

class PlanReviewTests(unittest.TestCase):
    def run_plan(self, outputs):
        service=Harness()
        service._structured_generate=Mock(side_effect=outputs)
        plan, audit, verification = service._build_verified_plan(
            'task-1', 'Fix the file without redesigning it', '', {'category':'task'}
        )
        return service, plan, json.loads(audit), verification

    def test_good_plan_is_kept_verbatim(self):
        service,plan,audit,verification=self.run_plan([
            DRAFT, {'improve':False,'confidence':0.91,'issues':[]}, {'accepted':True,'issues':[]}
        ])
        self.assertEqual(audit['plan'],DRAFT)
        self.assertEqual(audit['plan_self_review'],{'status':'checked_no_change','issues':[],'confidence':0.91,'passes':1})
        self.assertEqual(service._structured_generate.call_count,3)
        self.assertEqual(plan.steps[2].name,'Inspect')
        self.assertIn('Independent plan verification accepted',verification)

    def test_named_issue_triggers_one_targeted_revision_then_independent_verification(self):
        service,plan,audit,_=self.run_plan([
            DRAFT, {'improve':True,'confidence':0.87,'issues':['Patch step: avoid re-reading the same file twice']},
            REFINED, {'accepted':True,'issues':[]},
        ])
        self.assertEqual(audit['plan'],REFINED)
        self.assertEqual(audit['plan_self_review']['status'],'targeted_revision')
        self.assertEqual(len(audit['plan_self_review']['issues']),1)
        self.assertEqual(service._structured_generate.call_count,4)
        self.assertIn('avoid duplicate file reads',plan.steps[3].description)

    def test_identical_tightening_cannot_replace_good_plan(self):
        _,_,audit,_=self.run_plan([
            DRAFT, {'improve':True,'confidence':0.63,'issues':['Consider a tighter Patch step']}, DRAFT,
            {'accepted':True,'issues':[]},
        ])
        self.assertEqual(audit['plan'],DRAFT)
        self.assertEqual(audit['plan_self_review']['status'],'proposed_no_change')

    def test_malformed_review_falls_back_to_independent_verifier(self):
        service,_,audit,_=self.run_plan([
            DRAFT, {'improve':True,'confidence':0.74,'issues':[]}, {'accepted':True,'issues':[]}
        ])
        self.assertEqual(audit['plan'],DRAFT)
        self.assertEqual(audit['plan_self_review']['status'],'unavailable')
        self.assertEqual(service._structured_generate.call_count,3)

    def test_refinement_model_error_keeps_initial_draft(self):
        _,_,audit,_=self.run_plan([
            DRAFT, {'improve':True,'confidence':0.70,'issues':['Merge duplicate work']}, RuntimeError('model temporarily unavailable'),
            {'accepted':True,'issues':[]},
        ])
        self.assertEqual(audit['plan'],DRAFT)
        self.assertEqual(audit['plan_self_review']['status'],'unavailable')

    def test_independent_verifier_still_repairs_blocking_errors(self):
        service,_,audit,_=self.run_plan([
            DRAFT, {'improve':False,'confidence':0.83,'issues':[]},
            {'accepted':False,'issues':['No concrete step to test output']},
            REFINED, {'accepted':True,'issues':[]},
        ])
        self.assertEqual(audit['plan'],REFINED)
        self.assertEqual(audit['plan_self_review']['status'],'checked_no_change')
        self.assertEqual(service._structured_generate.call_count,5)

    def test_one_step_does_not_grow_or_get_rewritten_when_clean(self):
        _,plan,audit,_=self.run_plan([
            SIMPLE, {'improve':False,'confidence':0.96,'issues':[]}, {'accepted':True,'issues':[]}
        ])
        self.assertEqual(audit['plan'],SIMPLE)
        self.assertEqual([s.id for s in plan.steps],['plan-breakdown','verify-plan','step-01','final-verify'])

    def test_invalid_review_cannot_report_changes_while_saying_no(self):
        service=Harness();service._structured_generate=Mock(return_value={'improve':False,'confidence':0.90,'issues':['Unneeded step']})
        with self.assertRaisesRegex(ValueError,'disagree'):
            service._self_review_plan('Some request', DRAFT)

    def test_no_change_below_half_confidence_gets_second_scrutiny(self):
        service,_,audit,_=self.run_plan([
            DRAFT, {'improve':False,'confidence':0.35,'issues':[]},
            {'improve':False,'confidence':0.70,'issues':[]},
            {'accepted':True,'issues':[]},
        ])
        self.assertEqual(audit['plan'],DRAFT)
        self.assertEqual(audit['plan_self_review']['status'],'checked_no_change_after_second_review')
        self.assertEqual(audit['plan_self_review']['passes'],2)
        self.assertEqual(service._structured_generate.call_count,4)
        review_prompt=service._structured_generate.call_args_list[2].args[0]
        self.assertIn('previous',review_prompt)
        self.assertIn('below 0.50',review_prompt)
        self.assertIn('child-task',review_prompt)

    def test_second_scrutiny_identifies_subtask_decomposition(self):
        split={'title':'Patch a project','steps':[
            DRAFT['steps'][0],
            {'name':'Patch A','description':'Change one module of patch','verify':'Test A'},
            {'name':'Patch B','description':'Change independent module of patch','verify':'Test B'},
            DRAFT['steps'][-1],
        ]}
        service,plan,audit,_=self.run_plan([
            DRAFT, {'improve':False,'confidence':0.29,'issues':[]},
            {'improve':True,'confidence':0.82,'issues':[
                'Patch step hides two independent modules: split as independently verifiable Patch A and Patch B steps for reliable testing']},
            split, {'accepted':True,'issues':[]},
        ])
        self.assertEqual(audit['plan'],split)
        self.assertEqual(audit['plan_self_review']['status'],'targeted_revision')
        self.assertEqual(audit['plan_self_review']['passes'],2)
        self.assertEqual([step.name for step in plan.steps[2:6]],['Inspect','Patch A','Patch B','Deliver'])
        tighten_prompt=service._structured_generate.call_args_list[3].args[0]
        self.assertIn('child-task decomposition',tighten_prompt)

    def test_two_low_confidence_no_changes_cannot_claim_checked_fine(self):
        service,_,audit,_=self.run_plan([
            DRAFT, {'improve':False,'confidence':0.25,'issues':[]},
            {'improve':False,'confidence':0.42,'issues':[]},
            {'accepted':True,'issues':[]},
        ])
        self.assertEqual(audit['plan_self_review']['status'],'uncertain_independent_scrutiny_required')
        self.assertEqual(audit['plan_self_review']['confidence'],0.42)
        self.assertIn('INTERNAL PLANNING REVIEW ALERT',service._structured_generate.call_args_list[3].args[0])

    def test_missing_confidence_or_fake_certainty_rejected(self):
        service=Harness()
        for bad in [{'improve':False,'issues':[]},
                    {'improve':False,'confidence':True,'issues':[]},
                    {'improve':False,'confidence':1.9,'issues':[]},
                    {'improve':False,'confidence':-0.1,'issues':[]}]:
            service._structured_generate=Mock(return_value=bad)
            with self.assertRaisesRegex(ValueError,'invalid plan self-review'):
                service._self_review_plan('A task',DRAFT)

if __name__=='__main__':
    unittest.main(verbosity=2)
