import hashlib
import io
import time
import pytest
from pdfreader import font_download as client


class Response(io.BytesIO):
    pass


def run_job(qapp,tmp_path,monkeypatch,*,corrupt=False,cancel=False,url="https://raw.githubusercontent.com/notofonts/test/pinned/font.ttf"):
    raw=b"OTTO"+b"known fixed font"*500
    item={"url":url,"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"filename":"test-font.otf"}
    monkeypatch.setattr(client,"catalog",lambda:{"test":item})
    monkeypatch.setattr(client,"catalog_path",lambda _:None)
    monkeypatch.setattr(client,"cache_dir",lambda:tmp_path)
    requests=[]
    job=client.FontDownload("test")
    class Opener:
        def open(self,request,timeout):
            requests.append(request)
            assert request.data is None and "Authorization" not in request.headers
            response=Response(raw[:-1]+b"X" if corrupt else raw)
            if cancel:
                old=response.read
                def read(size):
                    value=old(size); job.cancel(); return value
                response.read=read
            return response
    monkeypatch.setattr(client,"build_opener",lambda *_:Opener())
    done=[]; errors=[]
    job.completed.connect(done.append); job.failed.connect(errors.append)
    job.start(); job.thread.join(3); qapp.processEvents()
    assert not job.thread.is_alive()
    return done,errors,requests,raw


def test_font_download_is_fixed_url_only_and_atomically_verified(qapp,tmp_path,monkeypatch):
    done,errors,requests,raw=run_job(qapp,tmp_path,monkeypatch)
    assert done==["library:test"] and not errors and len(requests)==1
    assert (tmp_path/"test-font.otf").read_bytes()==raw
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("cancel",[False,True])
def test_bad_or_cancelled_font_does_not_replace_existing_file(qapp,tmp_path,monkeypatch,cancel):
    destination=tmp_path/"test-font.otf"; destination.write_bytes(b"keep old valid file")
    done,errors,_,_=run_job(qapp,tmp_path,monkeypatch,corrupt=not cancel,cancel=cancel)
    assert not done and destination.read_bytes()==b"keep old valid file"
    assert not list(tmp_path.glob("*.part"))
    if not cancel: assert errors and "校验" in errors[0]


def test_document_cannot_redirect_font_request_to_another_host(qapp,tmp_path,monkeypatch):
    done,errors,requests,_=run_job(qapp,tmp_path,monkeypatch,url="https://example.invalid/font.ttf")
    assert not done and not requests and errors
