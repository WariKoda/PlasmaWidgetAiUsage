import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".github/workflows/branch-direction-guard.yml"
README = ROOT / "README.md"


class RepositoryBranchPolicyTests(unittest.TestCase):
    def test_main_pull_requests_reject_plasma5_as_source_branch(self):
        self.assertTrue(GUARD.exists(), "branch direction guard workflow is missing")
        source = GUARD.read_text(encoding="utf-8")

        self.assertIn("pull_request:", source)
        self.assertIn("branches:", source)
        self.assertIn("- main", source)
        self.assertIn("github.head_ref", source)
        self.assertIn("plasma-5.27", source)
        self.assertIn("exit 1", source)

    def test_readme_documents_one_way_maintenance_policy(self):
        source = README.read_text(encoding="utf-8")

        self.assertIn(
            "[Plasma 5.27 compatibility branch]"
            "(https://github.com/WariKoda/PlasmaWidgetAiUsage/tree/plasma-5.27)",
            source,
        )
        self.assertIn("## Maintenance branches", source)
        self.assertIn("`main` → `plasma-5.27`", source)
        self.assertIn("must never be merged back into `main`", source)


if __name__ == "__main__":
    unittest.main()
