import test from 'node:test';import assert from 'node:assert/strict';import {groups,filters,type Card} from '../src/directory.js';
test('UI02 groups one API page without duplicates',()=>{const a={id:'a',plan:'basic'} as Card,b={id:'b',plan:'promoted'} as Card;assert.deepEqual(groups([a,b]),{promoted:[b],basic:[a]});});
test('UI02 same hierarchy query for both groups',()=>assert.equal(filters('c','p','z').toString(),'country_id=c&province_id=p&zone_id=z'));
