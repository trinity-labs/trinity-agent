"""Offline adversarial regressions for edits, evidence, final gates and recovery."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ovh_verification import ChangeGuard, VerificationError, check_syntax, run_check, test_count

with patch.object(sys, 'argv', ['test-verification', '--billing-status']):
    spec = importlib.util.spec_from_file_location('verification_agent', ROOT / 'ovh-agent.py')
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)


class Verification(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'work'
        self.workspace.mkdir()
        self.file = self.workspace / 'app.py'
        self.file.write_text('VALUE = 1\n# existing user change\n')
        self.check = self.workspace / 'check.py'
        self.check.write_text("from pathlib import Path\nnamespace={}\nexec(compile(Path('app.py').read_text(), 'app.py', 'exec'), namespace)\nassert namespace['VALUE'] == 2, namespace['VALUE']\nprint('VALUE verified')\n")
        self.policy = self.workspace / '.ovh-agent-checks.json'
        self.policy.write_text(json.dumps({'version':1,'checks':[
            {'name':'Behavior','paths':['app.py'], 'argv':['{python}','check.py'],'protect':['check.py']}]}))
        self.events = []
        self.guard = ChangeGuard(self.workspace, self.root/'receipts', publish=self.events.append)

    def test_noop_contract_checks_run_once_without_manufacturing_a_change(self):
        self.file.write_text('VALUE = 2\n# existing user change\n')
        self.guard.ensure_checks(['Behavior'])
        self.assertEqual(self.guard.require_verified()['status'], 'passed')
        self.assertFalse(self.guard.state['changes'])
        count = len(self.guard.state['receipts'])
        self.guard.ensure_checks(['Behavior'])
        self.assertEqual(len(self.guard.state['receipts']), count)
        self.file.write_text('VALUE = 1\n# external edit\n')
        self.guard.observe(self.file)
        self.guard.ensure_checks(['Behavior'])
        self.assertFalse(self.guard.state['receipts'][-1]['passed'])

    def test_access_mode_external_paths_and_symlinks(self):
        outside = self.root / 'outside.txt'
        outside.write_text('outside')
        link = self.workspace / 'external-link'
        link.symlink_to(outside)
        with patch.object(backend, 'WORKSPACE', self.workspace), patch.object(backend, 'VERIFICATION_GUARD', None):
            for full in (False, True, False):
                with patch.object(backend, 'FULL_ACCESS', full):
                    for path in (outside, link):
                        if full:
                            self.assertEqual(backend.resolve_path(str(path)), outside)
                            self.assertEqual(backend.relative_name(outside), str(outside))
                        else:
                            with self.assertRaises(backend.ModelRetry):
                                backend.resolve_path(str(path))
                    self.assertEqual(backend.resolve_path('app.py'), self.file)
                    self.assertIn('Accès complet actif' if full else 'Mode restreint', backend.access_instructions())

    def test_external_access_notice_once_and_absolute_change_path(self):
        outside = self.root / 'outside.txt'
        events = []
        with patch.object(backend, 'WORKSPACE', self.workspace), patch.object(backend, 'FULL_ACCESS', True), patch.object(backend, 'VERIFICATION_GUARD', None), patch.object(backend, 'EXTERNAL_ACCESS_NOTICES', set()), patch.object(backend, 'emit', side_effect=events.append), patch.object(backend, 'read_task_state', return_value={}):
            backend.resolve_path(str(outside))
            backend.resolve_path(str(outside))
            backend.resolve_path(str(self.file))
            starts = [e for e in events if e['type'] == 'tool_start']
            self.assertEqual(len(starts), 1)
            self.assertEqual(starts[0]['title'], 'Accès hors projet')
            self.assertEqual(starts[0]['detail'], str(outside))
            backend.emit_file_change(backend.relative_name(outside), '+content')
            self.assertEqual(events[-1]['path'], str(outside))
            self.assertFalse(any(e['type'] == 'approval' for e in events))

    def test_external_project_checks_and_rollback_remain_active(self):
        # Use this fixture as an external project to a different workspace.
        other = self.root / 'other'
        other.mkdir()
        guard = ChangeGuard(other, self.root / 'external-receipts', allow_external=True)
        with patch.object(backend, 'WORKSPACE', other), patch.object(backend, 'FULL_ACCESS', True), patch.object(backend, 'VERIFICATION_GUARD', guard):
            self.assertEqual(backend.resolve_path(str(self.file)), self.file)
        original = self.file.read_text()
        guard.write(self.file, original, 'VALUE = 2\n# existing user change\n')
        self.assertEqual(guard.verify()['status'], 'passed')
        recovered = ChangeGuard.recover(other, guard.directory, allow_external=True)
        self.assertTrue(recovered.rollback()['restored'])
        self.assertEqual(self.file.read_text(), original)
        with self.assertRaises(VerificationError):
            guard._path(guard.directory / 'journal.json')
        with self.assertRaises(VerificationError):
            ChangeGuard(other, self.root / 'restricted').observe(self.file)

    def edit(self, value):
        self.guard.write(self.file, self.file.read_text(), f'VALUE = {value}\n# existing user change\n')

    def test_no_receipts_cannot_claim_test_success(self):
        for text in ('Aucun test automatisé n’a échoué.', 'Tests réussis.', 'All tests passed.',
                     'J’ai exécuté les tests du projet.'):
            with self.subTest(text=text), self.assertRaises(VerificationError):
                self.guard.validate_summary(text)
        for text in ('Tests non exécutés.', 'Je propose de lancer les tests.',
                     'Aucune modification effectuée : permission refusée pour app.py.'):
            self.assertEqual(self.guard.validate_summary(text), text)

    def test_edit_plan_is_not_completion_and_honest_blocker_is_allowed(self):
        self.guard.task_intent = 'edit'
        with self.assertRaisesRegex(VerificationError, 'aucun fichier'):
            self.guard.validate_summary('Si vous validez, je pourrai créer le catalogue.')
        text = 'Aucune modification effectuée : app.py est déjà conforme après lecture.'
        self.assertEqual(self.guard.validate_summary(text), text)
        self.edit(2)
        self.guard.require_verified()
        self.assertIn('Bilan des contrôles', self.guard.validate_summary('app.py modifié.'))

    def test_untracked_change_claim_is_rejected_even_if_intent_was_misclassified(self):
        self.guard.task_intent = 'question'
        with self.assertRaisesRegex(VerificationError, 'aucun changement'):
            self.guard.validate_summary('Le changement a été effectué et la règle CSS a été appliquée.')

    def test_build_turn_does_not_require_source_mutation_or_direct_a_fake_write(self):
        from ovh_request import analyze_request
        self.guard.task_intent = analyze_request(
            'Installe et publie le paquet déjà construit. Crée le tag annoté v1.2.3 sur le commit public.').intent
        text = 'Compilation exécutée sur les sources existantes.'
        self.assertEqual(self.guard.validate_summary(text), text)
        with self.assertRaisesRegex(VerificationError, 'ne réécris pas'):
            self.guard.validate_summary('Modification appliquée : le CSS contient la valeur demandée.')
        self.assertFalse(self.guard.state['changes'])

    def test_negated_change_is_allowed_without_masking_a_separate_success_claim(self):
        for text in ('Aucune modification effectuée.', 'Le fichier n’a pas été modifié.',
                     'No changes made.', 'Aucune modification appliquée : accès refusé.'):
            self.assertEqual(self.guard.validate_summary(text), text)
        self.guard.task_intent = 'edit'
        for text in ('Aucune modification n’a été effectuée.', 'Je n’ai effectué aucune modification.',
                     'Le fichier n’a pas été modifié.', 'No changes made.'):
            self.assertEqual(self.guard.validate_summary(text), text)
        with self.assertRaises(VerificationError):
            self.guard.validate_summary('Aucune modification effectuée. Le fichier est modifié.')
        with self.assertRaises(VerificationError):
            self.guard.validate_summary('Aucune modification effectuée mais le code est modifié.')

    def test_unconfigured_new_project_can_bootstrap_with_content_syntax_and_rollback(self):
        workspace = self.root / 'new-project'
        workspace.mkdir()
        guard = ChangeGuard(workspace, self.root / 'new-receipts')
        target = workspace / 'hello.py'
        guard.write(target, None, 'VALUE = 42\n')
        result = guard.require_verified()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['scope'], 'syntax_and_content')
        self.assertEqual(result['checks'], [])
        self.assertIn('Aucun test fonctionnel', guard.validate_summary('Fichier créé.'))
        with self.assertRaises(VerificationError):
            guard.validate_summary('Tous les tests sont réussis. Tests réussis.')
        self.assertEqual(guard.rollback()['restored'], [str(target)])
        self.assertFalse(target.exists())

    def test_basic_validation_does_not_certify_concurrent_changes_or_invalid_syntax(self):
        workspace = self.root / 'unconfigured'
        workspace.mkdir()
        target = workspace / 'app.py'
        target.write_text('VALUE = 1\n')
        guard = ChangeGuard(workspace, self.root / 'basic-receipts')
        with self.assertRaises(VerificationError):
            guard.write(target, target.read_text(), 'def invalid(\n')
        self.assertEqual(target.read_text(), 'VALUE = 1\n')
        guard.write(target, target.read_text(), 'VALUE = 2\n')
        target.write_text('VALUE = 3\n')
        with self.assertRaises(VerificationError):
            guard.require_verified()
        self.assertEqual(guard.rollback()['conflicts'], [str(target)])

    def basic_guard(self, name='logo.css', text='.logo { opacity: .5; }\n'):
        workspace = self.root / 'syntax-project'
        workspace.mkdir(exist_ok=True)
        guard = ChangeGuard(workspace, self.root / 'syntax-receipts')
        target = workspace / name
        guard.write(target, None, text)
        return guard, target

    def test_css_content_receipt_does_not_claim_unavailable_syntax(self):
        guard, target = self.basic_guard()
        report = guard.require_verified()
        self.assertEqual(report['status'], 'passed')
        self.assertFalse(guard.state['changes'][str(target)]['syntax_available'])
        self.assertEqual(report['syntax_unavailable'], [str(target)])
        self.assertEqual(report['syntax_checks'][0]['status'], 'unavailable')
        self.assertIsNone(report['syntax_checks'][0]['validator'])
        for text in ['Contrôle de syntaxe : ✅ passé', '**Contrôle de syntaxe** : ✅ passé',
                     'Contrôle de syntaxe :\n✅ passé', 'CSS syntax check passed.',
                     'La syntaxe de `logo.css` est vérifiée.',
                     'Syntaxe vérifiée sans tests fonctionnels.',
                     'Syntax check passed without functional tests.',
                     'J’ai vérifié la syntaxe.']:
            with self.subTest(text=text), self.assertRaisesRegex(VerificationError, 'sans preuve'):
                guard.validate_summary(text)
        for text in ['La syntaxe CSS n’a pas été vérifiée.', 'Syntaxe non vérifiée.',
                     'No syntax check passed.', 'Syntaxe CSS : contrôle non disponible.',
                     'Je n’ai pas vérifié la syntaxe.',
                     'Aucune modification effectuée. Je n’ai pas vérifié la syntaxe.',
                     'La syntaxe sera vérifiée après votre confirmation.',
                     'En CSS, la syntaxe valide est `opacity: 0.5;`.',
                     'Exemple de texte : `Syntaxe vérifiée`.']:
            output = guard.validate_summary(text)
            self.assertIn('Syntaxe non vérifiée pour ces fichiers : `logo.css`.', output)
            self.assertEqual(guard.validate_summary(output), output)

    def test_prewrite_syntax_is_not_a_final_receipt_and_external_edits_invalidate_it(self):
        guard, target = self.basic_guard('app.py', 'VALUE = 1\n')
        self.assertTrue(guard.state['changes'][str(target)]['syntax_available'])
        with self.assertRaisesRegex(VerificationError, 'sans preuve'):
            guard.validate_summary('Contrôle de syntaxe : ✅ passé')
        report = guard.require_verified()
        self.assertEqual(report['syntax_checks'][0]['status'], 'passed')
        self.assertEqual(report['syntax_checks'][0]['fingerprint'], guard.state['changes'][str(target)]['written'])
        output = guard.validate_summary('Contrôle de syntaxe : ✅ passé')
        self.assertIn('Syntaxe vérifiée sur le contenu final', output)
        self.assertEqual(guard.validate_summary(output), output)
        for text in ['La syntaxe de missing.js a été vérifiée.',
                     'La syntaxe de /missing/app.py a été vérifiée.']:
            with self.subTest(text=text), self.assertRaisesRegex(VerificationError, 'sans preuve'):
                guard.validate_summary(text)
        target.write_text('VALUE = 2\n')
        with self.assertRaisesRegex(VerificationError, 'sans preuve'):
            guard.validate_summary('La syntaxe de app.py est vérifiée.')

    def test_syntax_claims_preserve_exact_filenames_and_generated_footer_is_idempotent(self):
        guard, target = self.basic_guard('my_module.py', 'VALUE = 1\n')
        for name in ['__init__.py', 'my module.py', 'café.py', 'app.test.py']:
            guard.write(target.parent / name, None, 'VERSION = 1\n')
        guard.require_verified()
        for text in ['La syntaxe de my_module.py est vérifiée.',
                     'La syntaxe de `__init__.py` a été vérifiée.',
                     'La syntaxe de `my module.py` est vérifiée.', 'Terminé.']:
            with self.subTest(text=text):
                output = guard.validate_summary(text)
                self.assertIn('`my_module.py`', output)
                self.assertIn('`__init__.py`', output)
                self.assertEqual(guard.validate_summary(output), output)

    def test_mixed_formats_need_evidence_for_each_claimed_file(self):
        guard, css = self.basic_guard()
        script = css.parent / 'app.js'
        guard.write(script, None, 'const opacity = .5;\n')
        guard.require_verified()
        for text in ['Contrôle de syntaxe : ✅ passé', 'Syntaxe CSS vérifiée.',
                     'Syntaxe JavaScript de logo.css vérifiée.']:
            with self.subTest(text=text), self.assertRaisesRegex(VerificationError, 'sans preuve'):
                guard.validate_summary(text)
        output = guard.validate_summary('Syntaxe JavaScript vérifiée. Syntaxe CSS non vérifiée.')
        self.assertIn('Syntaxe vérifiée sur le contenu final : `app.js`.', output)
        self.assertIn('Syntaxe non vérifiée pour ces fichiers : `logo.css`.', output)
        self.assertEqual(guard.validate_summary(output), output)

    def test_configured_css_syntax_check_requires_explicit_kind_and_fresh_receipt(self):
        for kind in ['functional', 'syntax']:
            with self.subTest(kind=kind):
                work = self.root / kind
                work.mkdir()
                css = work / 'logo.css'
                css.write_text('.logo { opacity: 1; }\n')
                checker = work / 'check.py'
                checker.write_text("from pathlib import Path\nassert Path('logo.css').read_text() == '.logo { opacity: .5; }\\n'\n")
                policy = {'version': 1, 'checks': [{'name': 'CSS final', 'kind': kind,
                    'argv': ['{python}', 'check.py'], 'paths': ['logo.css'], 'protect': ['check.py']}]}
                (work / '.ovh-agent-checks.json').write_text(json.dumps(policy))
                guard = ChangeGuard(work, self.root / (kind + '-receipts'))
                guard.write(css, css.read_text(), '.logo { opacity: .5; }\n')
                report = guard.require_verified()
                self.assertTrue(report['checks'][0]['passed'])
                self.assertEqual(report['checks'][0]['kind'], kind)
                if kind == 'functional':
                    with self.assertRaisesRegex(VerificationError, 'sans preuve'):
                        guard.validate_summary('Syntaxe CSS vérifiée.')
                    self.assertEqual(report['syntax_unavailable'], [str(css)])
                else:
                    self.assertFalse(report['syntax_unavailable'])
                    self.assertEqual(report['syntax_checks'][0]['validator'], 'CSS final')
                    guard.validate_summary('Syntaxe CSS vérifiée.')
                    self.assertEqual(report['functional_coverage_missing'], [str(css)])
                    checker.write_text("print('weakened')\n")
                    with self.assertRaisesRegex(VerificationError, 'sans preuve'):
                        guard.validate_summary('Syntaxe CSS vérifiée.')

    def test_configured_syntax_kind_rejects_unknown_or_malformed_values(self):
        for kind in ['invented', [], None]:
            self.policy.write_text(json.dumps({'version': 1, 'checks': [
                {'argv': ['{python}', 'check.py'], 'kind': kind}]}))
            with self.assertRaisesRegex(VerificationError, 'kind'):
                self.guard.observe(self.file)

    def test_new_uncovered_script_is_rejected_before_any_write(self):
        target = self.workspace / 'manual-test.py'
        with self.assertRaisesRegex(VerificationError, 'verification_plan'):
            self.guard.write(target, None, 'print("PASS")\n')
        self.assertFalse(target.exists())
        self.assertFalse(self.guard.state['changes'])
        self.assertFalse(self.guard.state['receipts'])
        plan = self.guard.plan()
        self.assertEqual(plan['checks'][0]['argv'], [sys.executable, 'check.py'])
        self.assertIn(str(self.check), plan['checks'][0]['protected'])

    def test_nested_package_does_not_hide_enclosing_explicit_checks_policy(self):
        nested = self.workspace / 'nested'
        nested.mkdir()
        (nested / 'package.json').write_text('{}')
        target = nested / 'app.py'
        with self.assertRaisesRegex(VerificationError, 'Aucun contrôle'):
            self.guard.write(target, None, 'VALUE = 1\n')
        self.assertFalse(target.exists())

    def test_invalid_configuration_is_never_accepted_on_a_second_attempt(self):
        self.policy.write_text('{"version":1,"checks":[{"argv":["python"],"paths":"*.py"}]}')
        for _ in range(2):
            with self.assertRaisesRegex(VerificationError, 'listes'):
                self.guard.observe(self.file)
        self.assertFalse(self.guard.checks)

    def test_external_mode_change_is_preserved_by_rollback(self):
        self.edit(2)
        self.file.chmod(0o700)
        self.assertEqual(self.guard.rollback()['conflicts'], [str(self.file)])
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o700)

    def test_observed_dependency_invalidates_cached_success(self):
        dependency = self.workspace / 'dependency.py'
        dependency.write_text('VALUE = 1\n')
        self.guard.observe(dependency)
        self.edit(2)
        self.guard.require_verified()
        count = len(self.guard.state['receipts'])
        dependency.write_text('VALUE = 2\n')
        self.guard.require_verified()
        self.assertGreater(len(self.guard.state['receipts']), count)

    def test_unread_glob_inputs_invalidate_cached_success(self):
        parts = self.workspace / 'parts'
        parts.mkdir()
        dependency = parts / 'settings.json'
        dependency.write_text('"ok"')
        self.check.write_text("from pathlib import Path\nassert all(p.read_text() == '\"ok\"' for p in Path('parts').glob('*.json'))\nassert Path('parts/settings.json').is_file()\n")
        policy = json.loads(self.policy.read_text())
        policy['checks'][0]['paths'].append('parts/*.json')
        self.policy.write_text(json.dumps(policy))
        self.edit(2)
        self.assertEqual(self.guard.require_verified()['status'], 'passed')
        count = len(self.guard.state['receipts'])
        self.assertEqual(self.guard.require_verified()['status'], 'passed')
        self.assertEqual(len(self.guard.state['receipts']), count)
        dependency.write_text('"broken"')
        with self.assertRaises(VerificationError):
            self.guard.require_verified()
        dependency.write_text('"ok"')
        self.assertEqual(self.guard.require_verified()['status'], 'passed')
        extra = parts / 'new.json'
        extra.write_text('"broken"')
        with self.assertRaises(VerificationError):
            self.guard.require_verified()
        extra.unlink()
        self.assertEqual(self.guard.require_verified()['status'], 'passed')
        dependency.unlink()
        with self.assertRaises(VerificationError):
            self.guard.require_verified()

    def test_glob_input_changed_during_check_cannot_pass(self):
        dependency = self.workspace / 'dependency.json'
        dependency.write_text('1')
        policy = json.loads(self.policy.read_text())
        policy['checks'][0]['paths'].append('*.json')
        self.policy.write_text(json.dumps(policy))
        self.edit(2)
        def runner(*args):
            result = run_check(*args)
            dependency.write_text('2')
            return result
        self.guard.runner = runner
        with self.assertRaises(VerificationError):
            self.guard.require_verified()

    def test_glob_inputs_remain_relative_to_project_when_check_uses_subdirectory(self):
        (self.workspace / 'tests').mkdir()
        policy = json.loads(self.policy.read_text())
        policy['checks'][0].update(paths=['*.py'], cwd='tests', argv=['{python}', '-c',
            "from pathlib import Path; assert 'VALUE = 2' in Path('../app.py').read_text()"])
        self.policy.write_text(json.dumps(policy))
        self.edit(2)
        self.assertEqual(self.guard.require_verified()['status'], 'passed')

    def test_readonly_audit_can_report_executed_failures_without_claiming_success(self):
        self.guard.require_checks = True
        self.guard.observe(self.file)
        report = self.guard.require_verified()
        self.assertEqual(report['status'], 'observed_failure')
        self.assertNotEqual(report['checks'][0]['exit_code'], 0)
        self.assertFalse(report['universal_correctness'])

    def test_json_rejects_nonstandard_numeric_constants(self):
        for constant in ['NaN', 'Infinity', '-Infinity']:
            with self.assertRaises(VerificationError):
                check_syntax(Path('data.json'), '{"value":' + constant + '}')

    def test_invalid_syntax_never_touches_original_and_identical_retries_stop(self):
        before = self.file.read_bytes()
        for _ in range(2):
            with self.assertRaises(VerificationError):
                self.guard.write(self.file, before.decode(), 'if True:\nprint(1)\n')
        with self.assertRaisesRegex(VerificationError, 'Deux tentatives'):
            self.guard.write(self.file, before.decode(), 'if True:\nprint(1)\n')
        self.assertEqual(self.file.read_bytes(), before)
        self.assertFalse(self.guard.state['changes'])

    def test_real_red_green_checks_are_saved_and_final_verification_is_cached(self):
        self.edit(2)
        baseline = next(iter(self.guard.state['baseline'].values()))
        self.assertFalse(baseline['passed'])
        report = self.guard.require_verified()
        self.assertEqual(report['status'], 'passed')
        self.assertTrue(report['checks'][0]['passed'])
        self.assertEqual(report['checks'][0]['exit_code'], 0)
        count = len(self.guard.state['receipts'])
        self.guard.require_verified()
        self.assertEqual(len(self.guard.state['receipts']), count)
        journal = json.loads((self.root/'receipts/journal.json').read_text())
        self.assertEqual(journal['report']['fingerprints'][str(self.file)], self.guard.state['changes'][str(self.file)]['written'])

    def test_semantic_regression_fails_and_rollback_preserves_original_dirty_content(self):
        before = self.file.read_bytes()
        self.edit(3)
        with self.assertRaisesRegex(VerificationError, 'Tâche non validée'):
            self.guard.require_verified()
        result = self.guard.rollback()
        self.assertEqual(result['restored'], [str(self.file)])
        self.assertEqual(self.file.read_bytes(), before)

    def test_change_after_passed_test_requires_new_checks(self):
        self.edit(2)
        self.guard.require_verified()
        self.edit(3)
        with self.assertRaises(VerificationError):
            self.guard.require_verified()
        self.assertEqual(len(self.guard.state['receipts']), 3)  # one baseline, two revisions

    def test_external_edit_invalidates_result_and_is_never_rolled_back(self):
        self.edit(2)
        self.guard.require_verified()
        self.file.write_text('VALUE = 99 # concurrent user edit\n')
        with self.assertRaises(VerificationError):
            self.guard.require_verified()
        report = self.guard.rollback()
        self.assertEqual(report['conflicts'], [str(self.file)])
        self.assertIn('concurrent user edit', self.file.read_text())

    def test_weakened_reference_test_cannot_produce_success(self):
        self.edit(3)
        self.check.write_text("print('all good')\n")
        with self.assertRaisesRegex(VerificationError, 'Contrôles de référence modifiés'):
            self.guard.require_verified()

    def test_missing_suite_uses_limited_validation_but_missing_executable_is_not_success(self):
        self.policy.unlink()
        before = self.file.read_bytes()
        self.edit(2)
        report = self.guard.require_verified()
        self.assertEqual(report['scope'], 'syntax_and_content')
        self.assertFalse(report['checks'])
        self.guard.rollback()
        self.assertEqual(self.file.read_bytes(), before)
        result = run_check(['executable-that-does-not-exist'], self.workspace)
        self.assertFalse(result['passed'])
        self.assertIsNone(result['exit_code'])

    def test_killed_task_can_recover_backups_and_new_files_are_removed_only_if_unchanged(self):
        policy = json.loads(self.policy.read_text())
        policy['checks'][0]['paths'].append('new.py')
        self.policy.write_text(json.dumps(policy))
        before = self.file.read_bytes()
        self.edit(2)
        created = self.workspace / 'new.py'
        self.guard.write(created, None, 'VALUE = 3\n')
        recovered = ChangeGuard.recover(self.workspace, self.root/'receipts')
        recovered.rollback()
        self.assertEqual(self.file.read_bytes(), before)
        self.assertFalse(created.exists())

    def test_original_permissions_and_user_changes_survive_multiple_edits(self):
        self.file.chmod(0o750)
        before = self.file.read_bytes()
        self.edit(2)
        self.edit(3)
        self.guard.rollback()
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o750)

    def test_stale_edit_and_outside_paths_are_rejected(self):
        with self.assertRaises(VerificationError):
            self.guard.write(self.file, 'old version', 'VALUE = 2\n')
        with self.assertRaises(VerificationError):
            self.guard.write(self.root/'outside.py', None, 'VALUE = 2\n')
        self.assertFalse((self.root/'outside.py').exists())

    def test_readonly_audit_requires_actual_checks(self):
        guard = ChangeGuard(self.workspace, self.root/'audit', require_checks=True)
        with self.assertRaises(VerificationError):
            guard.require_verified()
        self.file.write_text('VALUE = 2\n')
        guard.observe(self.file)
        self.assertEqual(guard.require_verified()['status'], 'passed')

    def test_audit_without_configured_suite_reports_limits_after_actual_inspection(self):
        workspace = self.root / 'audit-without-tests'
        workspace.mkdir()
        guard = ChangeGuard(workspace, self.root / 'limited-audit', require_checks=True)
        with self.assertRaises(VerificationError):
            guard.require_verified()
        guard.observe(workspace)
        result = guard.require_verified()
        self.assertEqual(result['status'], 'not_configured')
        self.assertEqual(result['scope'], 'local_observations_only')
        self.assertFalse(result['checks'])
        output = guard.validate_summary('Le projet ne contient pas encore de suite configurée.')
        self.assertIn('Aucun test fonctionnel n’a été exécuté', output)
        with self.assertRaises(VerificationError):
            guard.validate_summary('Tests réussis.')
        self.assertFalse(guard.state['changes'])

    def test_general_question_does_not_require_irrelevant_test_execution(self):
        self.assertEqual(self.guard.require_verified()['status'], 'not_required')

    def test_checker_cannot_modify_source_and_still_certify_its_old_revision(self):
        self.edit(2)
        def mutating_runner(*_args):
            self.file.write_text('VALUE = 3\n')
            return {'passed':True,'exit_code':0,'output':'success','duration_ms':1}
        self.guard.runner = mutating_runner
        with self.assertRaisesRegex(VerificationError, 'résultats périmés'):
            self.guard.require_verified()

    def test_python_json_shell_and_javascript_syntax_validation(self):
        for name, source in [('x.py','return 1'),('x.json','{"missing":}'),('x.sh','if then'),('x.js','const = ;')]:
            with self.subTest(name=name), self.assertRaises(VerificationError):
                check_syntax(Path(name), source)
        for name, source in [('x.py','VALUE=1'),('x.json','{"value":1}'),('x.sh','echo ok'),('x.mjs','export const x=1;')]:
            self.assertNotIn('non disponible', check_syntax(Path(name), source))

    def test_timeout_is_not_a_success(self):
        result = run_check([sys.executable, '-c', 'import time; time.sleep(30)'], self.workspace, 1)
        self.assertFalse(result['passed'])
        self.assertTrue(result['timed_out'])

    def test_actual_pydantic_loop_rejects_premature_answer_and_rechecks_repair(self):
        from pydantic_ai import Agent, ModelRetry
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, RetryPromptPart
        self.edit(3)
        calls = []
        def model(messages, info):
            calls.append(messages)
            if len(calls) == 1:
                return ModelResponse(parts=[TextPart('Everything is fixed and verified.')])
            if len(calls) == 2:
                self.assertTrue(any(isinstance(part, RetryPromptPart) for message in messages for part in message.parts))
                return ModelResponse(parts=[ToolCallPart('repair', {}, tool_call_id='repair')])
            return ModelResponse(parts=[TextPart('Actual checks passed.')])
        def repair() -> str:
            """Repair the fixture."""
            self.edit(2)
            return 'Updated; verification pending.'
        agent = Agent(FunctionModel(model), tools=[repair], retries=2)
        @agent.output_validator
        def validate(output: str) -> str:
            try:
                self.guard.require_verified()
            except VerificationError as error:
                raise ModelRetry(str(error)) from error
            return output
        result = agent.run_sync('Fix the fixture')
        self.assertEqual(result.output, 'Actual checks passed.')
        self.assertEqual(result.usage.requests, 3)
        self.assertEqual(self.guard.state['status'], 'passed')

    def test_backend_file_tools_and_actual_validator_share_the_guard(self):
        from pydantic_ai import ModelRetry
        with ExitStack() as stack:
            for name, value in {'WORKSPACE':self.workspace,'VERIFICATION_GUARD':self.guard,
                                'JSON_MODE':False}.items():
                stack.enter_context(patch.object(backend, name, value))
            stack.enter_context(patch.object(backend, 'activity_start', return_value='edit'))
            stack.enter_context(patch.object(backend, 'activity_end'))
            stack.enter_context(patch.object(backend, 'emit'))
            self.assertIn('Erreur outil', backend.edit_file('app.py', 'VALUE = 1', 'if True:'))
            self.assertIn('VALUE = 1', self.file.read_text())
            self.assertIn('modifié', backend.edit_file('app.py', 'VALUE = 1', 'VALUE = 3'))
            with self.assertRaises(ModelRetry):
                backend.validate_task_output('Everything is fixed.')
            self.assertIn('écrit', backend.write_file('app.py', 'VALUE = 2\n'))
            summary = backend.validate_task_output('Checks passed.')
            self.assertTrue(summary.startswith('Checks passed.'))
            self.assertIn('Bilan des contrôles calculé par le backend', summary)
            self.assertEqual(backend.validate_task_output(summary), summary)

    def test_edit_rejection_is_structured_and_points_to_a_bounded_reread(self):
        from ovh_execution import WorkflowController
        original = 'Introduction\nHeading\n  keep indentation\nvalue\n' + ('untouched\n' * 200)
        target = self.workspace / 'notes.txt'
        target.write_text(original)
        with patch.object(backend, 'WORKSPACE', self.workspace), patch.object(backend, 'VERIFICATION_GUARD', self.guard):
            with patch.object(backend, 'request_approval') as approve:
                result = backend.edit_file('notes.txt', 'Heading\n keep indentation\nvalue', 'replacement')
                receipt = json.loads(result)
                self.assertEqual(receipt['status'], 'failed')
                self.assertEqual(receipt['category'], 'invalid_arguments')
                self.assertFalse(receipt['executed'])
                self.assertEqual(receipt['occurrences'], 0)
                self.assertEqual(receipt['candidate_lines'], [2])
                self.assertEqual(receipt['recovery']['tool'], 'read_file')
                arguments = receipt['recovery']['arguments']
                self.assertEqual(arguments['path'], 'notes.txt')
                self.assertLessEqual(arguments['start_line'], 2)
                self.assertLessEqual(arguments['end_line'] - arguments['start_line'], 40)
                self.assertLess(len(result), 1200)
                self.assertEqual(WorkflowController._outcome('edit_file', result, [])[0], 'invalid_arguments')
                approve.assert_not_called()
        self.assertEqual(target.read_text(), original)

    def test_invalid_edit_arguments_never_fall_back_to_inexact_or_multiple_replacement(self):
        original = self.file.read_text()
        with patch.object(backend, 'WORKSPACE', self.workspace), patch.object(backend, 'VERIFICATION_GUARD', self.guard):
            for old, new in [('', 'replacement'), ('VALUE = 1', None), ('NOT IN FILE', 'replacement')]:
                with self.subTest(old=old, new=new):
                    receipt = json.loads(backend.edit_file('app.py', old, new))
                    self.assertFalse(receipt['executed'])
                    self.assertEqual(receipt['category'], 'invalid_arguments')
                    self.assertEqual(self.file.read_text(), original)
            self.file.write_text('VALUE = 1\nVALUE = 1\n')
            receipt = json.loads(backend.edit_file('app.py', 'VALUE = 1', 'VALUE = 2'))
            self.assertEqual(receipt['occurrences'], 2)
            self.assertEqual(receipt['candidate_lines'], [1, 2])
            self.assertEqual(self.file.read_text(), 'VALUE = 1\nVALUE = 1\n')

    def test_tool_loop_recovers_from_an_edit_mismatch_without_sdk_retry_budget(self):
        from pydantic_ai import Agent
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
        calls = []
        def model(messages, info):
            calls.append(messages)
            if len(calls) == 1:
                return ModelResponse(parts=[ToolCallPart('edit_file', {
                    'path': 'app.py', 'old_text': 'VALUE=1', 'new_text': 'VALUE = 2'}, tool_call_id='bad-edit')])
            if len(calls) == 2:
                response = next(part for message in reversed(messages) for part in message.parts
                                if isinstance(part, ToolReturnPart) and part.tool_name == 'edit_file')
                self.assertEqual(json.loads(response.content)['category'], 'invalid_arguments')
                self.assertEqual(self.file.read_text(), 'VALUE = 1\n# existing user change\n')
                return ModelResponse(parts=[ToolCallPart('read_file', {'path': 'app.py'}, tool_call_id='reread')])
            if len(calls) == 3:
                return ModelResponse(parts=[ToolCallPart('edit_file', {
                    'path': 'app.py', 'old_text': 'VALUE = 1', 'new_text': 'VALUE = 2'}, tool_call_id='exact-edit')])
            return ModelResponse(parts=[TextPart('Corrected and verified.')])
        with ExitStack() as stack:
            for name, value in {'WORKSPACE': self.workspace, 'VERIFICATION_GUARD': self.guard,
                                'JSON_MODE': False}.items():
                stack.enter_context(patch.object(backend, name, value))
            for name in ('activity_start', 'activity_end', 'emit'):
                stack.enter_context(patch.object(backend, name))
            agent = Agent(FunctionModel(model), tools=[backend.read_file, backend.edit_file], retries=0)
            @agent.output_validator
            def validate(output: str) -> str:
                self.guard.require_verified()
                return output
            result = agent.run_sync('Set VALUE to 2, preserving the existing comment.')
        self.assertEqual(result.output, 'Corrected and verified.')
        self.assertEqual(result.usage.requests, 4)
        self.assertEqual(self.file.read_text(), 'VALUE = 2\n# existing user change\n')

    def test_real_backend_final_gate_and_recovery_without_paid_provider(self):
        archive = self.root / 'archive' / 'workspace' / 'conversation'
        archive.mkdir(parents=True)
        events = []
        def inputs(queue):
            queue.put({'type':'prompt','text':'Corrige app.py','model':'test-model','full_access':True})
            queue.put({'type':'shutdown'})
        def fake_run(*_args, **_kwargs):
            backend.edit_file('app.py', 'VALUE = 1', 'VALUE = 3')
            return SimpleNamespace(output='Everything fixed and tested!')
        with ExitStack() as stack:
            values = {'WORKSPACE':self.workspace, 'DATA_ROOT':self.root, 'ARCHIVE_ROOT':archive.parent,
                      'CONVERSATION_ID':archive.name,'JSON_MODE':False,'FULL_ACCESS':False,
                      'VERIFICATION_GUARD':None}
            for name, value in values.items():
                stack.enter_context(patch.object(backend,name,value))
            mocks = {'stdin_reader':{'side_effect':inputs},'emit':{'side_effect':events.append},
                     'load_history':{'return_value':[]},'read_snapshot':{'return_value':None},
                     'load_selected_model':{'return_value':'test-model'},'save_selected_model':{},
                     'is_selectable_model':{'return_value':True},'refresh_billing_async':{},
                     'current_budget_status':{'return_value':{}},'publish_budget':{},
                     'compact_history':{'return_value':([],{'before_chars':0})},
                     'prepare_task_context':{'return_value':([],{})},
                     'generation_settings':{'return_value':{}},
                     'model_capabilities':{'return_value':{'stream':False}},
                     'get_agent':{'return_value':SimpleNamespace(run_sync=fake_run)}}
            for name, settings in mocks.items():
                stack.enter_context(patch.object(backend,name,**settings))
            backend.main_json_lines()
            self.assertIsNone(backend.VERIFICATION_GUARD)
            self.assertEqual(backend.read_task_state()['status'],'interrupted')
        self.assertIn('VALUE = 1', self.file.read_text())
        self.assertFalse(any(event['type']=='response' for event in events))
        self.assertTrue(any(event.get('status')=='rolled_back' for event in events))
        self.assertTrue(any(event['type']=='error' and 'Tâche non validée' in event['text'] for event in events))


class TestEvidence(unittest.TestCase):
    def test_counts_require_runner_summaries_and_preserve_unknown(self):
        samples = [
            ('Ran 19 tests in 0.1s\n\nOK\n', 19),
            ('Ran 1 test in 0.1s\n\nFAILED (failures=1)\n', 1),
            ('# tests 30\n# pass 30\n# fail 0\n', 30),
            ('ℹ tests 30\nℹ pass 30\n', 30),
            ('================ 2 passed, 1 skipped in 0.20s ================', 3),
            ('1 failed, 2 passed, 4 deselected in 0.11s', 3),
            ('PASS: 107 assertions\nPASS: another block', None),
            ('Ran 8 tests in 0.1s\n\nOK\nRan 22 tests in 0.1s\n\nOK', None),
            ('Starting 107 tests', None),
        ]
        for output, count in samples:
            with self.subTest(output=output):
                self.assertEqual(test_count({'output':output,'exit_code':0})['reported_tests'], count)
        for flag in ['timed_out','output_truncated']:
            self.assertIsNone(test_count({'output':samples[0][0],'exit_code':0,flag:True})['reported_tests'])

    def make_guard(self, counts):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        work = Path(temp.name)/'work'
        work.mkdir()
        checks = []
        for i, count in enumerate(counts):
            script = work/f'check{i}.py'
            # Actual local process output fixtures; no network or provider calls.
            output = f'Ran {count} tests in 0.1s\n\nOK' if count is not None else 'PASS: configured checks'
            script.write_text('print('+repr(output)+')\n')
            checks.append({'name':f'Control {i}', 'argv':['{python}',script.name], 'paths':['*.py'], 'protect':[script.name]})
        (work/'.ovh-agent-checks.json').write_text(json.dumps({'version':1,'checks':checks}))
        guard = ChangeGuard(work, Path(temp.name)/'receipts')
        guard.observe(work)
        return work, guard

    def execute(self, work, guard, index):
        import shlex
        return guard.execute_configured(shlex.join([sys.executable,f'check{index}.py']), work)

    def test_original_107_claim_rejected_real_total_73_and_three_unknown(self):
        work, guard = self.make_guard([19,8,24,22,None,None,None])
        for i in range(7): self.execute(work,guard,i)
        summary = guard.test_summary()
        self.assertEqual(summary['reported_test_executions'],73)
        self.assertEqual(summary['uncounted_checks'],3)
        self.assertEqual(summary['passed_checks'],7)
        for text in ['Résumé des tests passés (107 tests total)',
                     'Le projet est en bon état avec aucune modification requise.',
                     '| Contrôle | Tests |\n| --- | --- |\n| Control 0 | 24 |']:
            with self.assertRaisesRegex(VerificationError, 'Bilan non justifié'):
                guard.validate_summary(text)
        result=guard.validate_summary('Les contrôles exécutés ont réussi.')
        self.assertIn('73 exécutions de tests décomptées',result)
        self.assertIn('3 contrôles sans décompte',result)
        self.assertEqual(guard.validate_summary(result),result)
        self.execute(work,guard,0)
        self.assertEqual(guard.test_summary()['reported_test_executions'],73)
        self.assertEqual(guard.test_summary()['checks_count'],7)

    def test_failure_replaces_success_and_changed_files_invalidate_evidence(self):
        work, guard = self.make_guard([19])
        self.execute(work,guard,0)
        (work/'check0.py').write_text('raise SystemExit(1)\n')
        self.assertEqual(guard.test_summary()['checks'][0]['status'],'stale')
        self.assertEqual(guard.test_summary()['reported_test_executions'],0)
        with self.assertRaises(VerificationError): guard.validate_summary('Tous les contrôles ont réussi.')
        self.execute(work,guard,0)  # Protected source tampering is refused, not counted as success.
        self.assertEqual(guard.test_summary()['checks'][0]['status'],'unavailable')
        self.assertEqual(guard.test_summary()['passed_checks'],0)

    def test_exact_configured_commands_do_not_interpret_shell_and_checks_are_reused(self):
        work, guard = self.make_guard([2])
        import shlex
        command=shlex.join([sys.executable,'check0.py'])
        self.assertIsNone(guard.execute_configured(command+'; echo OK',work))
        self.assertIsNone(guard.execute_configured(command+' | tail',work))
        self.assertFalse(guard.state['receipts'])
        self.execute(work,guard,0)
        guard.require_checks=True
        self.assertEqual(guard.require_verified()['status'],'passed')
        self.assertEqual(len(guard.state['receipts']),1)
        (work/'check0.py').write_text('raise SystemExit(1)\n')
        with self.assertRaises(VerificationError): guard.require_verified()

    def test_real_model_loop_retries_unjustified_final_and_appends_backend_summary(self):
        from pydantic_ai import Agent
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
        work, guard = self.make_guard([2])
        import shlex
        calls=[]
        def model(messages, info):
            calls.append(1)
            if len(calls)==1:
                return ModelResponse(parts=[ToolCallPart('run_command',{'command':shlex.join([sys.executable,'check0.py'])},tool_call_id='check')])
            if len(calls)==2:
                return ModelResponse(parts=[TextPart('107 tests réussis. Aucune modification requise.')])
            return ModelResponse(parts=[TextPart('Le contrôle exécuté a réussi.')])
        with patch.object(backend,'WORKSPACE',work), patch.object(backend,'VERIFICATION_GUARD',guard):
            agent=Agent(FunctionModel(model),tools=[backend.run_command],retries=2)
            agent.output_validator(backend.validate_task_output)
            result=agent.run_sync('Exécute le contrôle configuré et résume son résultat.')
        self.assertEqual(len(calls),3)
        self.assertNotIn('107',result.output)
        self.assertIn('2 exécutions de tests décomptées',result.output)
        self.assertIn('Bilan des contrôles calculé par le backend',result.output)
        self.assertEqual(len(guard.state['receipts']),1)


if __name__ == '__main__':
    unittest.main()
