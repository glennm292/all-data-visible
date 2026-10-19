# assets/app/ is the template install.py copies to system/apps/workspace_data/, test files
# and all. Collecting both copies in one root run is an import-file basename collision
# (export_test.py, test_workspace_data_ratchets.py), so the tests run from the installed copy.
collect_ignore = ["app"]
