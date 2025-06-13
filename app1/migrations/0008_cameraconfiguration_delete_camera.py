# Generated by Django 5.0.1 on 2024-10-06 06:20

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app1', '0007_camera'),
    ]

    operations = [
        migrations.CreateModel(
            name='CameraConfiguration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Give a name to this camera configuration', max_length=100, unique=True)),
                ('camera_source', models.IntegerField(default=0, help_text='Camera index (0 for default webcam)')),
                ('threshold', models.FloatField(default=0.6, help_text='Face recognition confidence threshold')),
                ('success_sound_path', models.CharField(help_text='Path to the success sound file', max_length=255)),
            ],
        ),
        migrations.DeleteModel(
            name='Camera',
        ),
    ]
