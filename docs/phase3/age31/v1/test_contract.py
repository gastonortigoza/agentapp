"""Mutation checks of the contract. These are NOT SaaS or PostgreSQL tests."""
import copy,json,unittest
from pathlib import Path
from validator import check_contract,check_manifest_binding
HERE=Path(__file__).resolve().parent
def read(n):return json.loads((HERE/n).read_text(encoding='utf-8'))
class ContractTests(unittest.TestCase):
    def setUp(self):
        self.doc=read('contract.json');self.cfg=read('review-criteria.json');self.cases=read('acceptance.json')
    def valid(self,d):return not check_contract(d,self.cfg,self.cases)
    def test_correct_document(self):self.assertTrue(self.valid(self.doc))
    def test_nullable_known_DTO(self):self.assertTrue(self.valid(self.doc))
    def test_empty_request_204_response(self):self.assertTrue(self.valid(self.doc))
    def test_global_payload_hash_unique(self):
        self.doc['data']['tables']['idempotency']['unique']=[['payload_hash']];self.assertFalse(self.valid(self.doc))
    def test_global_key_unique(self):
        self.doc['data']['tables']['idempotency']['unique']=[['key']];self.assertFalse(self.valid(self.doc))
    def test_bad_scope(self):
        self.doc['subscriptions']['idempotency']['scope']=['key'];self.assertFalse(self.valid(self.doc))
    def test_production_flag_guard(self):
        self.doc['subscriptions']['production_guard']='check_client_simulated';self.assertFalse(self.valid(self.doc))
    def test_client_origin(self):
        self.doc['subscriptions']['origin_source']='client';self.assertFalse(self.valid(self.doc))
    def test_missing_saved_response(self):
        del self.doc['data']['tables']['idempotency']['columns']['response_body'];self.assertFalse(self.valid(self.doc))
    def test_mismatching_date(self):
        self.doc['subscriptions']['dates']=['starts_at','expires_at'];self.assertFalse(self.valid(self.doc))
    def test_bad_first_lock(self):
        self.doc['subscriptions']['lock_table']='subscriptions';self.assertFalse(self.valid(self.doc))
    def test_missing_endpoint(self):
        self.doc['subscriptions']['endpoint']='POST /api/absent';self.assertFalse(self.valid(self.doc))
    def test_undefined_nullable_DTO(self):
        self.doc['api']['routes'][0]['response']['items']='$Absent|null';self.assertFalse(self.valid(self.doc))
    def test_private_DTO_leak(self):
        self.doc['api']['dtos']['PublicCard']['birth_date']='date';self.assertFalse(self.valid(self.doc))
    def test_broken_geographic_FK(self):
        self.doc['data']['tables']['profiles']['foreign_keys'][1]='(zone_id,province_id,country_id) REFERENCES zones(id,unknown,country_id)';self.assertFalse(self.valid(self.doc))
    def test_nonunique_FK_target(self):
        self.doc['data']['tables']['zones']['unique']=[];self.assertFalse(self.valid(self.doc))
    def test_absent_command(self):
        del self.doc['manifest']['commands']['migrate'];self.assertFalse(self.valid(self.doc))
    def test_shell_argv(self):
        self.doc['manifest']['commands']['unit']['argv']=['powershell','-Command','anything'];self.assertFalse(self.valid(self.doc))
    def test_invalid_string_argv(self):
        self.doc['manifest']['commands']['unit']['argv']='npm test';self.assertFalse(self.valid(self.doc))
    def test_path_escape(self):
        for cwd in ['../external','C:/external','/external','frontend/../../external']:
            doc=copy.deepcopy(self.doc);doc['manifest']['commands']['fe']['cwd']=cwd;self.assertFalse(self.valid(doc))
    def test_enabled_deployment(self):
        self.doc['manifest']['enabled']=True;self.assertFalse(self.valid(self.doc))
    def test_network_escape(self):
        self.doc['manifest']['network']['allow'].append('example.com:443');self.assertFalse(self.valid(self.doc))
    def test_secret_literal(self):
        self.doc['manifest']['secrets_ref'][0]='postgresql://password';self.assertFalse(self.valid(self.doc))
    def test_unproven_test_claim(self):
        self.cases[0]['status']='passed';self.assertFalse(self.valid(self.doc))
    def test_nonexistent_acceptance_route(self):
        self.cases[0]['path']='/api/invented';self.assertFalse(self.valid(self.doc))
    def test_binding_and_changed_content(self):
        manifest=read('manifest.json');policy=read('policy.json');business=read('business-decisions.json')
        # Source hash preserves the original user record bytes, not a reserialization.
        source=HERE/'business-source.json'
        args=(self.doc,policy,business,(HERE/'contract.json').read_bytes(),(HERE/'policy.json').read_bytes(),source.read_bytes())
        self.assertEqual([],check_manifest_binding(manifest,*args))
        manifest['requirement']['contract_sha256']='0'*64
        self.assertIn('requirement identity/hash',check_manifest_binding(manifest,*args))
if __name__=='__main__':unittest.main(verbosity=2)
