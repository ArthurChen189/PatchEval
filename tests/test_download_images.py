import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import download_images


class DownloadImagesMainTest(unittest.TestCase):
    @mock.patch.object(download_images, "batch_pull_images", return_value=(230, 0))
    def test_success_exits_zero(self, _batch_pull_images):
        self.assertEqual(download_images.main(), 0)

    @mock.patch.object(download_images, "batch_pull_images", return_value=(229, 1))
    def test_partial_failure_exits_nonzero(self, _batch_pull_images):
        self.assertEqual(download_images.main(), 1)

    @mock.patch.object(download_images, "batch_pull_images", return_value=(0, 0))
    def test_setup_failure_exits_nonzero(self, _batch_pull_images):
        self.assertEqual(download_images.main(), 1)

    def test_missing_image_list_command_exits_nonzero(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "download_images.py"
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("does not exist", result.stderr)


class DownloadImagesPullTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(download_images.docker, "from_env")
        self.from_env = patcher.start()
        self.addCleanup(patcher.stop)
        self.client = self.from_env.return_value
        self.logger = mock.Mock()

    def test_pull_uses_environment_client_and_closes_it(self):
        self.client.api.pull.return_value = [
            {"status": "Downloading"}, {"status": "Download complete"},
        ]
        with mock.patch.object(download_images.docker, "APIClient") as api_client:
            self.assertEqual(
                download_images.pull_one_image("test:latest", self.logger),
                (True, "test:latest"),
            )
            api_client.assert_not_called()
        self.client.api.pull.assert_called_once_with(
            "test:latest", stream=True, decode=True,
        )
        self.client.close.assert_called_once()

    def test_stream_errors_fail_and_close_client(self):
        for event in (
            {"error": "disk full"},
            {"errorDetail": {"message": "disk full"}},
        ):
            with self.subTest(event=event):
                self.client.reset_mock()
                self.client.api.pull.return_value = [event]
                self.assertEqual(
                    download_images.pull_one_image("test:latest", self.logger),
                    (False, "test:latest"),
                )
                self.client.close.assert_called_once()

    def test_connection_failure_closes_client(self):
        self.client.ping.side_effect = RuntimeError("unavailable")
        self.assertEqual(
            download_images.pull_one_image("test:latest", self.logger),
            (False, "test:latest"),
        )
        self.client.api.pull.assert_not_called()
        self.client.close.assert_called_once()

    def test_client_creation_failure_returns_failure(self):
        self.from_env.side_effect = RuntimeError("bad endpoint")
        self.assertEqual(
            download_images.pull_one_image("test:latest", self.logger),
            (False, "test:latest"),
        )

    def test_batch_connection_failure_closes_client(self):
        self.client.ping.side_effect = RuntimeError("unavailable")
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "images.txt"
            manifest.write_text("test:latest\n")
            with (
                mock.patch.object(download_images, "pull_one_image") as pull,
                mock.patch.object(download_images.logging, "basicConfig"),
                mock.patch.object(download_images.logging, "FileHandler"),
                mock.patch.object(download_images.logging, "getLogger",
                                  return_value=self.logger),
            ):
                self.assertEqual(download_images.batch_pull_images(
                    images_file=str(manifest),
                    log_file=str(Path(directory) / "pull.log"),
                ), (0, 0))
                pull.assert_not_called()
        self.client.close.assert_called_once()

    def test_batch_logs_storage_and_closes_connection(self):
        self.client.info.return_value = {"DockerRootDir": "/mnt/local/docker"}
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "images.txt"
            manifest.write_text("test:latest\n")
            with (
                mock.patch.object(download_images, "pull_one_image",
                                  return_value=(True, "test:latest")),
                mock.patch.object(download_images.logging, "basicConfig"),
                mock.patch.object(download_images.logging, "FileHandler"),
                mock.patch.object(download_images.logging, "getLogger",
                                  return_value=self.logger),
            ):
                self.assertEqual(download_images.batch_pull_images(
                    images_file=str(manifest),
                    log_file=str(Path(directory) / "pull.log"),
                    max_workers=1,
                ), (1, 0))
        self.logger.info.assert_any_call(
            "Docker connection success; storage: %s", "/mnt/local/docker",
        )
        self.client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
