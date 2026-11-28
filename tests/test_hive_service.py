import unittest
from types import SimpleNamespace
from unittest.mock import patch

from TCLIService.ttypes import TOperationState
from thrift.transport.TTransport import TTransportException

from hive_service import (
    HiveClient, HiveConfig, HiveConfigurationError, HiveQueryError,
    HiveResultTooLarge, HiveTimeout, HiveUnavailable,
)


class ConfigTests(unittest.TestCase):
    def test_environment_and_defaults(self):
        config = HiveConfig.from_env({"HIVE_HOST": "localhost", "HIVE_PORT": "10001", "HIVE_AUTH": "nosasl"})
        self.assertEqual(config.host, "localhost")
        self.assertEqual(config.port, 10001)
        self.assertEqual(config.auth, "NOSASL")
        self.assertEqual(config.query_timeout, 60)

    def test_invalid_config(self):
        for env in ({}, {"HIVE_PORT": "abc"}, {"HIVE_PORT": "0"}, {"HIVE_QUERY_TIMEOUT": "0"},
                    {"HIVE_AUTH": "unknown"}, {"HIVE_DATABASE": "bad-name"}, {"HIVE_CACHE_ENTRIES": "-1"}):
            with self.subTest(env=env), self.assertRaises(HiveConfigurationError):
                HiveConfig.from_env({"HIVE_HOST": "" if not env else "localhost", **env})


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.client = HiveClient(HiveConfig("localhost"))
        socket_patch = patch("hive_service.client.TSocket.TSocket")
        connect_patch = patch("hive_service.client.hive.connect")
        self.socket = socket_patch.start().return_value
        self.addCleanup(socket_patch.stop)
        self.connect = connect_patch.start()
        self.addCleanup(connect_patch.stop)
        self.connection = self.connect.return_value
        self.cursor = self.connection.cursor.return_value
        self.cursor.poll.return_value = SimpleNamespace(operationState=TOperationState.FINISHED_STATE)
        self.cursor.description = [("patch_id",), ("n",)]
        self.cursor.fetchmany.side_effect = [[("sdv_20201221_15", 1)], []]

    def test_query_passes_params_and_releases_resources(self):
        sql = "SELECT patch_id AS patch_id, 1 AS n FROM steam_dw.dim_patch WHERE patch_id=%s"
        params = ("sdv_20201221_15",)
        self.assertEqual(self.client.query(sql, params), [{"patch_id": params[0], "n": 1}])
        self.cursor.execute.assert_called_once_with(sql, params, async_=True)
        self.assertEqual(self.connect.call_args.kwargs["configuration"], {"hive.query.timeout.seconds": "60"})
        self.socket.setTimeout.assert_any_call(10000)
        self.cursor.close.assert_called_once()
        self.connection.close.assert_called_once()
        self.socket.close.assert_called_once()

    def test_bounded_results_fail_instead_of_truncating(self):
        self.cursor.fetchmany.side_effect = [[("a", 1), ("b", 2)]]
        with self.assertRaises(HiveResultTooLarge):
            self.client.query("SELECT 1", max_rows=1)
        self.cursor.close.assert_called_once()
        self.connection.close.assert_called_once()

    def test_duplicate_columns_rejected(self):
        self.cursor.description = [("n",), ("n",)]
        with self.assertRaises(HiveQueryError):
            self.client.query("SELECT 1")
        self.cursor.close.assert_called_once()

    def test_timeout_cancels_and_closes(self):
        self.cursor.poll.return_value = SimpleNamespace(operationState=TOperationState.TIMEDOUT_STATE)
        with self.assertRaises(HiveTimeout):
            self.client.query("SELECT 1")
        self.cursor.cancel.assert_called_once()
        self.cursor.close.assert_called_once()
        self.connection.close.assert_called_once()

    def test_disconnected_transport_is_not_reused(self):
        self.cursor.poll.side_effect = TTransportException(TTransportException.END_OF_FILE)
        with self.assertRaises(HiveUnavailable):
            self.client.query("SELECT 1")
        self.cursor.close.assert_not_called()
        self.connection.close.assert_not_called()
        self.socket.close.assert_called_once()

    def test_failed_connect_closes_socket(self):
        self.connect.side_effect = TTransportException(TTransportException.TIMED_OUT)
        with self.assertRaises(HiveTimeout):
            self.client.query("SELECT 1")
        self.socket.close.assert_called_once()

    def test_query_error_still_closes_connection(self):
        self.cursor.execute.side_effect = ValueError("bad parameters")
        with self.assertRaises(HiveQueryError):
            self.client.query("SELECT 1")
        self.cursor.close.assert_called_once()
        self.connection.close.assert_called_once()


class CacheTests(unittest.TestCase):
    def test_cache_is_opt_in_isolated_and_expires(self):
        client = HiveClient(HiveConfig("localhost"))
        with patch.object(client, "_query", return_value=[{"n": 1}]) as run:
            with patch("hive_service.client.monotonic", return_value=10) as clock:
                result = client.query("SELECT %s AS n", (1,), cache_ttl=2)
                result[0]["n"] = 99
                self.assertEqual(client.query("SELECT %s AS n", (1,), cache_ttl=2), [{"n": 1}])
                self.assertEqual(run.call_count, 1)
                client.query("SELECT %s AS n", (2,), cache_ttl=2)
                self.assertEqual(run.call_count, 2)
                clock.return_value = 13
                client.query("SELECT %s AS n", (1,), cache_ttl=2)
                self.assertEqual(run.call_count, 3)
                client.query("SELECT %s AS n", (1,))
                self.assertEqual(run.call_count, 4)
                client.clear_cache()
                client.query("SELECT %s AS n", (1,), cache_ttl=2)
                self.assertEqual(run.call_count, 5)

    def test_cache_capacity_and_large_results(self):
        client = HiveClient(HiveConfig("localhost", cache_entries=1))
        with patch.object(client, "_query", return_value=[{"n": 1}]) as run:
            client.query("SELECT 1", cache_ttl=30)
            client.query("SELECT 2", cache_ttl=30)
            client.query("SELECT 1", cache_ttl=30)
            self.assertEqual(run.call_count, 3)
            run.return_value = [{"n": 1}] * 1001
            client.query("SELECT 3", cache_ttl=30)
            client.query("SELECT 3", cache_ttl=30)
            self.assertEqual(run.call_count, 5)


if __name__ == "__main__":
    unittest.main()
