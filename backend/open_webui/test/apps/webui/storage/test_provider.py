import io
import os
import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from open_webui.storage import provider
from gcp_storage_emulator.server import create_server
from google.cloud import storage
import docker
import time
import requests
import importlib
from open_webui import config
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError, ResourceExistsError


def mock_upload_dir(monkeypatch, tmp_path):
    """Fixture to monkey-patch the UPLOAD_DIR and create a temporary directory."""
    directory = tmp_path / "uploads"
    directory.mkdir()
    monkeypatch.setattr(provider, "UPLOAD_DIR", str(directory))
    return directory


def test_imports():
    provider.StorageProvider
    provider.LocalStorageProvider
    provider.S3StorageProvider
    provider.GCSStorageProvider
    provider.AzureBlobStorageProvider

    provider.Storage


def test_get_storage_provider():
    Storage = provider.get_storage_provider("local")
    assert isinstance(Storage, provider.LocalStorageProvider)
    Storage = provider.get_storage_provider("s3")
    assert isinstance(Storage, provider.S3StorageProvider)
    Storage = provider.get_storage_provider("gcs")
    assert isinstance(Storage, provider.GCSStorageProvider)
    with pytest.raises(RuntimeError):
        provider.get_storage_provider("invalid")


def test_class_instantiation():
    with pytest.raises(TypeError):
        provider.StorageProvider()
    with pytest.raises(TypeError):

        class Test(provider.StorageProvider):
            pass

        Test()
    provider.LocalStorageProvider()
    provider.S3StorageProvider()
    provider.GCSStorageProvider()


class TestLocalStorageProvider:
    Storage = provider.LocalStorageProvider()
    file_content = b"test content"
    file_bytesio = io.BytesIO(file_content)
    filename = "test.txt"
    filename_extra = "test_exyta.txt"
    file_bytesio_empty = io.BytesIO()

    def test_upload_file(self, monkeypatch, tmp_path):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        contents, file_path = self.Storage.upload_file(self.file_bytesio, self.filename)
        assert (upload_dir / self.filename).exists()
        assert (upload_dir / self.filename).read_bytes() == self.file_content
        assert contents == self.file_content
        assert file_path == str(upload_dir / self.filename)
        with pytest.raises(ValueError):
            self.Storage.upload_file(self.file_bytesio_empty, self.filename)

    def test_get_file(self, monkeypatch, tmp_path):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        file_path = str(upload_dir / self.filename)
        file_path_return = self.Storage.get_file(file_path)
        assert file_path == file_path_return

    def test_delete_file(self, monkeypatch, tmp_path):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        (upload_dir / self.filename).write_bytes(self.file_content)
        assert (upload_dir / self.filename).exists()
        file_path = str(upload_dir / self.filename)
        self.Storage.delete_file(file_path)
        assert not (upload_dir / self.filename).exists()

    def test_delete_all_files(self, monkeypatch, tmp_path):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        (upload_dir / self.filename).write_bytes(self.file_content)
        (upload_dir / self.filename_extra).write_bytes(self.file_content)
        self.Storage.delete_all_files()
        assert not (upload_dir / self.filename).exists()
        assert not (upload_dir / self.filename_extra).exists()


@mock_aws
class TestS3StorageProvider:

    def __init__(self):
        self.Storage = provider.S3StorageProvider()
        self.Storage.bucket_name = "my-bucket"
        self.s3_client = boto3.resource("s3", region_name="us-east-1")
        self.file_content = b"test content"
        self.filename = "test.txt"
        self.filename_extra = "test_exyta.txt"
        self.file_bytesio_empty = io.BytesIO()
        super().__init__()

    def test_upload_file(self, monkeypatch, tmp_path):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        # S3 checks
        with pytest.raises(Exception):
            self.Storage.upload_file(io.BytesIO(self.file_content), self.filename)
        self.s3_client.create_bucket(Bucket=self.Storage.bucket_name)
        contents, s3_file_path = self.Storage.upload_file(
            io.BytesIO(self.file_content), self.filename
        )
        object = self.s3_client.Object(self.Storage.bucket_name, self.filename)
        assert self.file_content == object.get()["Body"].read()
        # local checks
        assert (upload_dir / self.filename).exists()
        assert (upload_dir / self.filename).read_bytes() == self.file_content
        assert contents == self.file_content
        assert s3_file_path == "s3://" + self.Storage.bucket_name + "/" + self.filename
        with pytest.raises(ValueError):
            self.Storage.upload_file(self.file_bytesio_empty, self.filename)

    def test_get_file(self, monkeypatch, tmp_path):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        self.s3_client.create_bucket(Bucket=self.Storage.bucket_name)
        contents, s3_file_path = self.Storage.upload_file(
            io.BytesIO(self.file_content), self.filename
        )
        file_path = self.Storage.get_file(s3_file_path)
        assert file_path == str(upload_dir / self.filename)
        assert (upload_dir / self.filename).exists()

    def test_delete_file(self, monkeypatch, tmp_path):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        self.s3_client.create_bucket(Bucket=self.Storage.bucket_name)
        contents, s3_file_path = self.Storage.upload_file(
            io.BytesIO(self.file_content), self.filename
        )
        assert (upload_dir / self.filename).exists()
        self.Storage.delete_file(s3_file_path)
        assert not (upload_dir / self.filename).exists()
        with pytest.raises(ClientError) as exc:
            self.s3_client.Object(self.Storage.bucket_name, self.filename).load()
        error = exc.value.response["Error"]
        assert error["Code"] == "404"
        assert error["Message"] == "Not Found"

    def test_delete_all_files(self, monkeypatch, tmp_path):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        # create 2 files
        self.s3_client.create_bucket(Bucket=self.Storage.bucket_name)
        self.Storage.upload_file(io.BytesIO(self.file_content), self.filename)
        object = self.s3_client.Object(self.Storage.bucket_name, self.filename)
        assert self.file_content == object.get()["Body"].read()
        assert (upload_dir / self.filename).exists()
        assert (upload_dir / self.filename).read_bytes() == self.file_content
        self.Storage.upload_file(io.BytesIO(self.file_content), self.filename_extra)
        object = self.s3_client.Object(self.Storage.bucket_name, self.filename_extra)
        assert self.file_content == object.get()["Body"].read()
        assert (upload_dir / self.filename).exists()
        assert (upload_dir / self.filename).read_bytes() == self.file_content

        self.Storage.delete_all_files()
        assert not (upload_dir / self.filename).exists()
        with pytest.raises(ClientError) as exc:
            self.s3_client.Object(self.Storage.bucket_name, self.filename).load()
        error = exc.value.response["Error"]
        assert error["Code"] == "404"
        assert error["Message"] == "Not Found"
        assert not (upload_dir / self.filename_extra).exists()
        with pytest.raises(ClientError) as exc:
            self.s3_client.Object(self.Storage.bucket_name, self.filename_extra).load()
        error = exc.value.response["Error"]
        assert error["Code"] == "404"
        assert error["Message"] == "Not Found"

        self.Storage.delete_all_files()
        assert not (upload_dir / self.filename).exists()
        assert not (upload_dir / self.filename_extra).exists()


class TestGCSStorageProvider:
    Storage = provider.GCSStorageProvider()
    Storage.bucket_name = "my-bucket"
    file_content = b"test content"
    filename = "test.txt"
    filename_extra = "test_exyta.txt"
    file_bytesio_empty = io.BytesIO()

    @pytest.fixture(scope="class")
    def setup(self):
        host, port = "localhost", 9023

        server = create_server(host, port, in_memory=True)
        server.start()
        os.environ["STORAGE_EMULATOR_HOST"] = f"http://{host}:{port}"

        gcs_client = storage.Client()
        bucket = gcs_client.bucket(self.Storage.bucket_name)
        bucket.create()
        self.Storage.gcs_client, self.Storage.bucket = gcs_client, bucket
        yield
        bucket.delete(force=True)
        server.stop()

    def test_upload_file(self, monkeypatch, tmp_path, setup):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        # catch error if bucket does not exist
        with pytest.raises(Exception):
            self.Storage.bucket = monkeypatch(self.Storage, "bucket", None)
            self.Storage.upload_file(io.BytesIO(self.file_content), self.filename)
        contents, gcs_file_path = self.Storage.upload_file(
            io.BytesIO(self.file_content), self.filename
        )
        object = self.Storage.bucket.get_blob(self.filename)
        assert self.file_content == object.download_as_bytes()
        # local checks
        assert (upload_dir / self.filename).exists()
        assert (upload_dir / self.filename).read_bytes() == self.file_content
        assert contents == self.file_content
        assert gcs_file_path == "gs://" + self.Storage.bucket_name + "/" + self.filename
        # test error if file is empty
        with pytest.raises(ValueError):
            self.Storage.upload_file(self.file_bytesio_empty, self.filename)

    def test_get_file(self, monkeypatch, tmp_path, setup):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        contents, gcs_file_path = self.Storage.upload_file(
            io.BytesIO(self.file_content), self.filename
        )
        file_path = self.Storage.get_file(gcs_file_path)
        assert file_path == str(upload_dir / self.filename)
        assert (upload_dir / self.filename).exists()

    def test_delete_file(self, monkeypatch, tmp_path, setup):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        contents, gcs_file_path = self.Storage.upload_file(
            io.BytesIO(self.file_content), self.filename
        )
        # ensure that local directory has the uploaded file as well
        assert (upload_dir / self.filename).exists()
        assert self.Storage.bucket.get_blob(self.filename).name == self.filename
        self.Storage.delete_file(gcs_file_path)
        # check that deleting file from gcs will delete the local file as well
        assert not (upload_dir / self.filename).exists()
        assert self.Storage.bucket.get_blob(self.filename) == None

    def test_delete_all_files(self, monkeypatch, tmp_path, setup):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        # create 2 files
        self.Storage.upload_file(io.BytesIO(self.file_content), self.filename)
        object = self.Storage.bucket.get_blob(self.filename)
        assert (upload_dir / self.filename).exists()
        assert (upload_dir / self.filename).read_bytes() == self.file_content
        assert self.Storage.bucket.get_blob(self.filename).name == self.filename
        assert self.file_content == object.download_as_bytes()
        self.Storage.upload_file(io.BytesIO(self.file_content), self.filename_extra)
        object = self.Storage.bucket.get_blob(self.filename_extra)
        assert (upload_dir / self.filename_extra).exists()
        assert (upload_dir / self.filename_extra).read_bytes() == self.file_content
        assert (
            self.Storage.bucket.get_blob(self.filename_extra).name
            == self.filename_extra
        )
        assert self.file_content == object.download_as_bytes()

        self.Storage.delete_all_files()
        assert not (upload_dir / self.filename).exists()
        assert not (upload_dir / self.filename_extra).exists()
        assert self.Storage.bucket.get_blob(self.filename) == None
        assert self.Storage.bucket.get_blob(self.filename_extra) == None


class TestAzureBlobStorageProvider:

    file_content = b"test content"
    filename = "test.txt"
    filename_extra = "test_exyta.txt"
    file_bytesio_empty = io.BytesIO()
    
    def is_azurite_ready(self, url: str, attempts: int = 15, delay: float = 2) -> bool:
        """Checks if Azurite is ready by making HTTP requests."""
        for _ in range(attempts):
            try:
                response = requests.get(url)
                if response.status_code == 200 or response.status_code == 400:
                    return True
            except requests.exceptions.RequestException:
                pass
            time.sleep(delay)
        return False
    
    @pytest.fixture(scope="class")
    def storage_provider(self):
        """Starts Azurite, sets env vars, initializes provider, ensures container stop."""
        client = docker.from_env()
        container = None 
        try:
            container = client.containers.run(
                "mcr.microsoft.com/azure-storage/azurite",
                ports={'10000/tcp': 10000, '10001/tcp': 10001, '10002/tcp': 10002},
                remove=True,
                detach=True,
            )

            azurite_url = "http://127.0.0.1:10000/devstoreaccount1"
            if not self.is_azurite_ready(azurite_url):
                raise RuntimeError("Azurite failed to start within the allowed time.")  # Raise to trigger finally

            # Set environment variables (same as before)
            config.AZURE_STORAGE_ACCOUNT = "http://127.0.0.1:10000/devstoreaccount1"  # Replace with your actual config
            config.AZURE_STORAGE_ACCESS_KEY = (
                "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
                "K1SZFPTOtr/KBHBeksoGMGw=="
            )
        
            config.AZURE_CONTAINER_NAME = "test-container"  

            # Reload the provider module to ensure clean initialization with new env vars
            # This is necessary because the Azure provider's state depends on env vars
            # that are processed during module import.
            # While other test providers are changing the internal state of the provider instance as 
            # part of the test, the Azure provider needs to have its state reset by reloading the module.
            importlib.reload(provider)        
            yield provider.AzureBlobStorageProvider()

        except Exception as e:  # Catch any exceptions during setup
            print(f"Exception during setup: {e}")
            raise  
        finally:  # Ensure container stop in all cases
            if container: 
                print("Stopping Azurite container")
                container.stop()


    def test_upload_file(self, monkeypatch, tmp_path, storage_provider):  # Inject storage_provider
        print(f"test_upload_file self id: {id(self)}")

        # Use the injected storage_provider (which is self.Storage) directly:
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        container_client = storage_provider.container_client  # Use storage_provider here
        try:
            container_client.create_container()
        except ResourceExistsError:
            pass

        contents, azure_file_path = storage_provider.upload_file(  # Use storage_provider here
            io.BytesIO(self.file_content), self.filename
        )
        blob_client = storage_provider.blob_service_client.get_blob_client(  # Use storage_provider here
            storage_provider.container_name, self.filename  # Use storage_provider here
        )
        downloaded_data = blob_client.download_blob().readall()
        assert downloaded_data == self.file_content
        assert (upload_dir / self.filename).exists()
        assert (upload_dir / self.filename).read_bytes() == self.file_content
        assert contents == self.file_content
        assert azure_file_path == "azure://" + storage_provider.container_name + "/" + self.filename  # Use storage_provider here

        with pytest.raises(ValueError):
            storage_provider.upload_file(self.file_bytesio_empty, self.filename)  # Use storage_provider here


    def test_get_file(self, monkeypatch, tmp_path, storage_provider):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        container_client = storage_provider.container_client  # Use storage_provider here
        try:
            container_client.create_container()
        except ResourceExistsError:
            pass

        contents, azure_file_path = storage_provider.upload_file(io.BytesIO(self.file_content), self.filename)
        file_path = storage_provider.get_file(azure_file_path)
        assert file_path == str(upload_dir / self.filename)
        assert (upload_dir / self.filename).exists()

    def test_delete_file(self, monkeypatch, tmp_path, storage_provider):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        blob_service_client = storage_provider.blob_service_client
        container_client = storage_provider.container_client  
        try:
            container_client.create_container()
        except ResourceExistsError:
            pass

        contents, azure_file_path = storage_provider.upload_file(io.BytesIO(self.file_content), self.filename)
        assert (upload_dir / self.filename).exists()

        storage_provider.delete_file(azure_file_path)
        assert not (upload_dir / self.filename).exists()

        blob_client = blob_service_client.get_blob_client(storage_provider.container_name, self.filename)
        with pytest.raises(ResourceNotFoundError):
            blob_client.get_blob_properties()

    def test_delete_all_files(self, monkeypatch, tmp_path, storage_provider):
        upload_dir = mock_upload_dir(monkeypatch, tmp_path)
        blob_service_client = storage_provider.blob_service_client
        container_client = storage_provider.container_client  
        try:
            container_client.create_container()
        except ResourceExistsError:
            pass

        # Upload two files
        storage_provider.upload_file(io.BytesIO(self.file_content), self.filename)
        assert (upload_dir / self.filename).exists()
        storage_provider.upload_file(io.BytesIO(self.file_content), self.filename_extra)
        assert (upload_dir / self.filename_extra).exists()

        storage_provider.delete_all_files()
        assert not (upload_dir / self.filename).exists()
        assert not (upload_dir / self.filename_extra).exists()

        # Verify that blobs are deleted in Azure
        blob_client1 = blob_service_client.get_blob_client(storage_provider.container_name, self.filename)
        blob_client2 = blob_service_client.get_blob_client(storage_provider.container_name, self.filename_extra)

        with pytest.raises(ResourceNotFoundError):
            blob_client1.get_blob_properties()
        with pytest.raises(ResourceNotFoundError):
            blob_client2.get_blob_properties()
