name "python-cinderclient"
pypi_name = "python-cinderclient"
default_version "1.0.9"

dependency "python"

build do
  command "#{install_dir}/embedded/bin/pip install -I #{pypi_name}==#{default_version}"
end
